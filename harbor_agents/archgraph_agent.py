"""ARCHGRAPH → Harbor agent adapter.

Thin adapter that plugs the ARCHGRAPH harness into Harbor's `BaseAgent` interface.
The ARCHGRAPH orchestration (model calls, intent-graph Skill/Rule retrieval,
self-verification loop) runs on the HOST; this adapter only translates the
"commands to run" into `environment.exec()` calls inside the sandbox, per the
guide (terminal-bench-eval-guide.md §4).

Design:
- `name()` -> "archgraph" (shown in the leaderboard agent column).
- `setup()`: optional host-side prep (no-op by default).
- `run()`: ARCHGRAPH harness main loop:
    1. Retrieve a matching Skill from the intent graph (skill lookup).
    2. If a skill matches -> execute its command plan in the sandbox.
    3. Otherwise -> model-driven ReAct loop (via litellm + QWEN_KEY).
    4. Self-verify before finishing (run the task's check script if present).
    5. Populate `context` with the result as we go (survives timeouts).

Workspace-relative entrypoint:  harbor_agents/archgraph_agent.py:ArchGraphAgent
"""

from __future__ import annotations

import json
import os
import re
import shlex
import sys

from harbor.agents.base import BaseAgent

try:  # litellm ships with harbor; used by the model-driven fallback path
    import litellm
except Exception:  # pragma: no cover - optional
    litellm = None


class ArchGraphAgent(BaseAgent):
    """ARCHGRAPH harness adapter for Harbor (Terminal-Bench runner)."""

    #: Number of model-driven rounds before giving up.
    MAX_ROUNDS = 12

    #: Timeout (seconds) for each sandbox command.
    CMD_TIMEOUT = 120.0

    @staticmethod
    def name() -> str:
        """The name of the agent (shown on the leaderboard)."""
        return "archgraph"

    def version(self) -> str | None:
        """The version of the agent."""
        return "0.1.0"

    async def setup(self, environment) -> None:
        """Run commands to set up the agent & its tools (host side)."""
        # ARCHGRAPH runs on the host; nothing to install in the sandbox here.
        # Future: copy Skill/Rule packs or helper scripts into the sandbox.
        return None

    # ------------------------------------------------------------------ #
    # Skill lookup (host-side, from the intent-graph Skills)
    # ------------------------------------------------------------------ #
    @staticmethod
    def _match_skill(instruction: str) -> dict | None:
        """Best-effort local skill registry.

        In the full ARCHGRAPH harness this reads Skills from the intent graph
        (design/KG/SystemArchitecture.json). The openssl-selfsigned-cert skill
        is distilled during Round-0 bootstrap; more skills get added as rounds
        progress.
        """
        skills = ArchGraphAgent._load_graph_skills()
        text = instruction.lower()
        for skill in skills:
            for pattern in skill.get("patterns", []):
                if pattern in text:
                    return skill
        return None

    @staticmethod
    def _load_graph_skills() -> list[dict]:
        """Load Skill elements from the local intent graph (host side)."""
        skills: list[dict] = []
        # Resolve the repo root from this module's location (robust to Harbor's cwd).
        module_dir = os.path.dirname(os.path.abspath(__file__))  # <repo>/harbor_agents
        repo_root = os.path.dirname(module_dir)
        graph_path = os.path.join(repo_root, "design", "KG", "SystemArchitecture.json")
        if not os.path.exists(graph_path):
            return skills
        try:
            with open(graph_path, "r", encoding="utf-8") as fh:
                graph = json.load(fh)
            for element in graph.get("elements", []):
                if element.get("type") != "Skill":
                    continue
                attrs = {a.get("name"): a.get("value", "") for a in element.get("attributes", [])}
                if attrs.get("trigger_patterns"):
                    skills.append(
                        {
                            "id": element.get("id"),
                            "name": element.get("name", ""),
                            "patterns": [
                                p.strip().lower()
                                for p in attrs["trigger_patterns"].split(",")
                                if p.strip()
                            ],
                            "plan": attrs.get("command_plan", ""),
                            "verify_cmd": attrs.get("verify_command", ""),
                        }
                    )
        except Exception:  # pragma: no cover - graph read must never crash the agent
            pass
        return skills

    # ------------------------------------------------------------------ #
    # Sandbox exec helpers
    # ------------------------------------------------------------------ #
    async def _exec(self, environment, command: str) -> str:
        """Run one bash command in the sandbox and return combined output."""
        try:
            result = await environment.exec(command, timeout_sec=self.CMD_TIMEOUT)
            output = getattr(result, "output", None) or ""
            returncode = getattr(result, "returncode", None)
            self._log(f"$ {command[:120]}\nrc={returncode}\n{output[-1500:]}")
            return output
        except Exception as exc:  # surface failures without crashing the loop
            self._log(f"EXEC ERROR {type(exc).__name__}: {exc}")
            return f"<exec error: {type(exc).__name__}: {exc}>"

    def _log(self, message: str) -> None:
        """Best-effort structured logging (host side) for debugging."""
        logger = getattr(self, "logger", None)
        if logger is not None:
            try:
                logger.info(message)
                return
            except Exception:
                pass
        print(f"[archgraph] {message}", flush=True)

    # ------------------------------------------------------------------ #
    # Model-driven fallback (host-side ReAct loop)
    # ------------------------------------------------------------------ #
    @staticmethod
    def _model_call(messages: list[dict]) -> str:
        """Call the underlying model via litellm using QWEN_KEY (DashScope)."""
        if litellm is None:
            return "litellm unavailable"
        api_key = os.environ.get("QWEN_KEY") or os.environ.get("DASHSCOPE_API_KEY") or ""
        model = os.environ.get("ARCHGRAPH_MODEL", "dashscope/qwen3-32b")
        kwargs = {
            "model": model,
            "messages": messages,
            "api_key": api_key,
            "temperature": 0.2,
            "max_tokens": 2000,
        }
        if api_key:
            kwargs["api_base"] = os.environ.get("QWEN_BASE_URL")
        try:
            response = litellm.completion(**kwargs)
            return response.choices[0].message.content or ""
        except Exception as exc:  # pragma: no cover
            return f"<model error: {exc}>"

    @staticmethod
    def _extract_commands(text: str) -> list[str]:
        """Pull bash commands out of the model's answer (```bash blocks)."""
        blocks = re.findall(r"```(?:bash|sh)?\s*(.*?)```", text, re.DOTALL)
        if blocks:
            return [b.strip() for b in blocks if b.strip()]
        # Fall back to lines that look like shell commands.
        candidates = []
        for line in text.splitlines():
            line = line.strip()
            if line and not line.startswith(("#", "//", "```")) and " " not in line[:1] and "=" not in line[:3]:
                candidates.append(line)
        return candidates[:1] if candidates else []

    async def _model_loop(self, instruction: str, environment, context, messages: list[dict]) -> None:
        """ReAct loop: ask the model for commands, run them, feed output back."""
        rounds = 0
        while rounds < self.MAX_ROUNDS:
            rounds += 1
            reply = self._model_call(messages)
            commands = self._extract_commands(reply)
            if not commands:
                messages.append({"role": "assistant", "content": reply})
                messages.append(
                    {
                        "role": "user",
                        "content": "No runnable command was provided. Either output a ```bash block with the next command(s), or say DONE if the task is complete.",
                    }
                )
                continue
            outputs = []
            for cmd in commands:
                out = await self._exec(environment, cmd)
                outputs.append(f"$ {cmd}\n{out[:4000]}")
            joined = "\n".join(outputs)
            context.metadata = {**(context.metadata or {}), "last_command": commands[-1]}
            if "DONE" in reply.upper():
                break
            messages.append({"role": "assistant", "content": reply})
            messages.append({"role": "user", "content": joined[-12000:]})

    # ------------------------------------------------------------------ #
    # Main entrypoint
    # ------------------------------------------------------------------ #
    async def run(self, instruction: str, environment, context) -> None:
        """Runs the ARCHGRAPH harness in the sandbox for one task trial."""
        messages: list[dict] = [{"role": "system", "content": (
            "You are a terminal agent. Work inside the sandbox at /app. "
            "Always output the next command(s) inside a ```bash code block. "
            "Before finishing, verify your work and only then output DONE."
        )}, {"role": "user", "content": instruction}]

        # 1) Try the ARCHGRAPH skill path first (cross-task persistent knowledge).
        skill = self._match_skill(instruction)
        if skill and skill.get("plan"):
            context.metadata = {**(context.metadata or {}), "skill_id": skill.get("id")}
            plan = skill["plan"].replace("\\n", "\n")
            # Write the plan into the sandbox as a script, then execute it. This
            # keeps heredocs (e.g. creating check_cert.py) intact.
            write_cmd = (
                "cat > /tmp/archgraph_skill.sh << 'ARCHGRAPH_SKILL_EOF'\n"
                + plan
                + "\nARCHGRAPH_SKILL_EOF\n"
                + "chmod +x /tmp/archgraph_skill.sh"
            )
            await self._exec(environment, write_cmd)
            await self._exec(environment, "bash /tmp/archgraph_skill.sh")
            if skill.get("verify_cmd"):
                await self._exec(environment, skill["verify_cmd"])
            return

        # 2) Fall back to the model-driven ReAct loop.
        await self._model_loop(instruction, environment, context, messages)

        # 3) Best-effort self-verification if a check script exists in the sandbox.
        await self._exec(environment, "cd /app && [ -f check_cert.py ] && python check_cert.py || true")
