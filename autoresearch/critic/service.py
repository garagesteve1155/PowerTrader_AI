from __future__ import annotations

from typing import Any, Dict, List

from autoresearch.models import Hypothesis, Recommendation


def _patch_for_hypothesis(hypothesis: Hypothesis) -> Dict[str, Any]:
    changes = hypothesis.parameter_target.get("config_patch")
    if not changes:
        path = hypothesis.parameter_target.get("config_path")
        changes = {path: hypothesis.parameter_target.get("proposed_value")} if path else {}
    return {
        "target_file": hypothesis.parameter_target["target_file"],
        "changes": changes,
    }


def risk_review(
    hypotheses: List[Hypothesis],
    score_payload: Dict[str, Any],
) -> Dict[str, Any]:
    scored_index = {row["hypothesis_id"]: row for row in score_payload.get("ranked", [])}
    findings: List[Dict[str, Any]] = []
    recommendations: List[Recommendation] = []
    for rank, hypothesis in enumerate(hypotheses, start=1):
        score = scored_index.get(hypothesis.hypothesis_id, {})
        reasons: List[str] = []
        if score.get("avg_oos_expectancy_delta", 0.0) <= 0:
            reasons.append("No out-of-sample expectancy improvement.")
        if score.get("avg_trade_count", 0.0) < 3:
            reasons.append("Sample size too small after applying the change.")
        if score.get("avg_drawdown_delta", 0.0) < -0.5:
            reasons.append("Drawdown worsened in the tested windows.")
        if score.get("regime_robustness", 0.0) < 0.5:
            reasons.append("Performance is not directionally consistent across live and paper.")

        accepted = not reasons
        findings.append(
            {
                "hypothesis_id": hypothesis.hypothesis_id,
                "title": hypothesis.title,
                "status": "accepted" if accepted else "rejected",
                "reasons": reasons,
                "score": score,
            }
        )
        recommendations.append(
            Recommendation(
                rank=rank,
                hypothesis_id=hypothesis.hypothesis_id,
                title=hypothesis.title,
                evidence_summary=(
                    f"Average out-of-sample expectancy delta: {score.get('avg_oos_expectancy_delta', 0.0):+.2f}% "
                    f"with robustness {score.get('regime_robustness', 0.0):.2f}."
                ),
                config_patch=_patch_for_hypothesis(hypothesis) if accepted else None,
                rollout_plan=[
                    "Paper trade the approved profile for one weekly cycle.",
                    "Promote to small capital only if paper results match the research expectation.",
                    "Scale gradually while monitoring drawdown and regime drift.",
                ],
                rejection_reasons=reasons,
                status="accepted" if accepted else "rejected",
            )
        )
    recommendations.sort(key=lambda item: (item.status == "accepted", -item.rank), reverse=True)
    for index, recommendation in enumerate(recommendations, start=1):
        recommendation.rank = index
    return {
        "findings": findings,
        "recommendations": [item.to_dict() for item in recommendations],
    }
