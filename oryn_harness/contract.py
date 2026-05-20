"""Négociation Generator ⇄ Evaluator pour produire le contrat d'un sprint.

C'est la clé du pattern. Avant qu'une seule ligne de code soit écrite,
les deux agents s'accordent sur ce que "done" signifie — via des
fichiers markdown sur disque. C'est ce qui empêche le Generator de
bâcler et l'Evaluator d'être vague.
"""
from __future__ import annotations

from rich.console import Console

from .claude_runner import ClaudeRunner
from .config import HarnessConfig
from .prompts import EVALUATOR_PROMPT, GENERATOR_PROMPT
from .state import Sprint, StateManager

console = Console()


class ContractNegotiator:
    """Orchestre 2-3 rounds de négo entre Generator et Evaluator."""

    def __init__(self, config: HarnessConfig, state: StateManager):
        self.config = config
        self.state = state

    def _gen_runner(self) -> ClaudeRunner:
        return ClaudeRunner(
            cwd=self.config.workdir,
            model=self.config.generator_model,
            system_prompt=GENERATOR_PROMPT,
            permission_mode=self.config.permission_mode,
            timeout=self.config.cli_timeout,
            allowed_tools=["Read", "Write", "Edit", "Glob", "Grep"],
        )

    def _eval_runner(self) -> ClaudeRunner:
        return ClaudeRunner(
            cwd=self.config.workdir,
            model=self.config.evaluator_model,
            system_prompt=EVALUATOR_PROMPT,
            permission_mode=self.config.permission_mode,
            timeout=self.config.cli_timeout,
            allowed_tools=["Read", "Write", "Edit", "Glob", "Grep"],
        )

    def negotiate(self, sprint: Sprint, max_rounds: int = 10) -> str:
        """Lance la négociation. Retourne le contrat final."""
        console.rule(f"[bold magenta]CONTRACT NEGOTIATION — sprint {sprint.id}")

        # Round 1 : Generator propose
        gen_prompt = f"""Tu commences le sprint **{sprint.id} — {sprint.title}**.

Description : {sprint.description}

Lis d'abord :
- `.oryn/spec.md` (la vision globale)
- `.oryn/feature_list.json` (le contexte des autres sprints)

Puis PROPOSE un contrat dans `.oryn/contracts/sprint_{sprint.id}.md`.
Format imposé :
```markdown
# Contrat sprint {sprint.id} — PROPOSITION GENERATOR

## Ce que je propose de livrer
- <bullet précis>
...

## Comment je propose qu'EVALUATOR teste
- <commande / scénario>
...

## Edge cases que je vais couvrir
- ...

## Edge cases que je NE couvrirai PAS (et pourquoi)
- ...
```

Tu ne codes RIEN à ce stade. Juste le contrat. Sois honnête sur les limites.
"""
        gen_result = self._gen_runner().run(gen_prompt)
        self.state.write_trace(f"contract_gen_round1_{sprint.id}", gen_result.raw_stdout)

        # Rounds suivants : Evaluator pousse, Generator répond
        for round_num in range(1, max_rounds + 1):
            console.print(f"[dim]Round {round_num} : Evaluator review[/dim]")

            eval_prompt = f"""Mode : NÉGOCIATION DE CONTRAT.

Tu lis `.oryn/contracts/sprint_{sprint.id}.md` (la proposition du Generator).
Tu lis aussi `.oryn/spec.md` pour avoir le contexte.

Sprint actuel : **{sprint.id} — {sprint.title}**
{sprint.description}

Critique cette proposition :
- Le scope est-il assez précis ?
- Les tests proposés couvrent-ils les vrais risques ?
- Y a-t-il des edge cases ignorés ?
- L'esthétique est-elle assez spécifique (pas générique) ?

Puis RÉÉCRIS le contrat dans le même fichier avec 20-30 critères granulaires
testables (CRIT-XX, DESIGN-XX, EDGE-XX). Sois IMPITOYABLE sur la précision.

Si Generator a écrit "le bouton doit être joli", remplace par
"le bouton utilise un fond #XXX, ombre Y, hover state qui Z".
"""
            eval_result = self._eval_runner().run(eval_prompt)
            self.state.write_trace(f"contract_eval_round{round_num}_{sprint.id}", eval_result.raw_stdout)

            if round_num < max_rounds:
                # Generator répond une dernière fois
                gen_prompt = f"""Evaluator a réécrit `.oryn/contracts/sprint_{sprint.id}.md`.

Lis-le. Si certains critères sont IRRÉALISTES pour ce sprint (trop de scope, hors-sujet,
techniquement impossible avec la stack choisie), ajoute en bas du fichier une section :

```markdown
## Objections du Generator
- CRIT-XX: <pourquoi c'est irréaliste, et contre-proposition>
```

Sinon, accepte tel quel et écris à la fin :
```
CONTRACT_ACCEPTED
```
"""
                gen_result = self._gen_runner().run(gen_prompt)
                self.state.write_trace(f"contract_gen_round{round_num+1}_{sprint.id}", gen_result.raw_stdout)

                if "CONTRACT_ACCEPTED" in gen_result.text:
                    console.print("[green]✓ Contract accepted by Generator[/green]")
                    break

        final_contract = self.state.read_contract(sprint.id)
        console.print(f"[green]✓ Contrat sprint {sprint.id} négocié[/green]")
        return final_contract
