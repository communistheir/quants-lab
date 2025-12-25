import sys
import os
from pathlib import Path
import pandas as pd
import logging
import json
import argparse
import numpy as np
from datetime import datetime

# Add project root to path
project_root = Path(__file__).resolve().parent.parent
sys.path.append(str(project_root))

from core.backtesting.optimizer import StrategyOptimizer

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def parse_args():
    parser = argparse.ArgumentParser(description="Summarize Optimization Results")
    parser.add_argument("--study_name", type=str, help="Specific study name to analyze. If not provided, uses the latest.")
    parser.add_argument("--output_dir", type=str, default="backtesting_result", help="Directory to save the report.")
    return parser.parse_args()


def analyze_correlations(df, target_col="value"):
    """Calculate correlation between parameters and the target metric."""
    # Filter for numeric columns only
    numeric_df = df.select_dtypes(include=[np.number])

    if target_col not in numeric_df.columns:
        return pd.Series(dtype=float)

    correlations = numeric_df.corr()[target_col].drop(target_col)
    # Remove 'number' if present
    if "number" in correlations:
        correlations = correlations.drop("number")

    return correlations.sort_values(ascending=False)


def check_boundaries(study, best_params):
    """Check if best parameters are close to the search space boundaries."""
    recommendations = []

    # Get distributions from the first trial (assuming consistent search space)
    if not study.trials:
        return []

    # Find a trial that has distributions (some might be pruned early)
    distributions = None
    for trial in study.trials:
        if trial.distributions:
            distributions = trial.distributions
            break

    if not distributions:
        return []

    for param_name, param_value in best_params.items():
        if param_name not in distributions:
            continue

        dist = distributions[param_name]

        # Check FloatDistribution and IntDistribution
        # Note: Optuna distributions have .high and .low attributes
        if hasattr(dist, "high") and hasattr(dist, "low"):
            low = dist.low
            high = dist.high
            step = getattr(dist, "step", None)

            # Calculate range
            r = high - low
            if r == 0:
                continue

            # Check lower bound (within 5% of range)
            if abs(param_value - low) / r < 0.05:
                recommendations.append(
                    {
                        "param": param_name,
                        "issue": "Hit Lower Bound",
                        "current_value": param_value,
                        "bound": low,
                        "suggestion": f"Decrease lower bound for {param_name}",
                    }
                )

            # Check upper bound
            if abs(param_value - high) / r < 0.05:
                recommendations.append(
                    {
                        "param": param_name,
                        "issue": "Hit Upper Bound",
                        "current_value": param_value,
                        "bound": high,
                        "suggestion": f"Increase upper bound for {param_name}",
                    }
                )

    return recommendations


def generate_recommendations(correlations, boundary_issues):
    """Generate actionable recommendations based on analysis."""
    recs = []

    # 1. Boundary Recommendations
    for issue in boundary_issues:
        recs.append(
            f"- **Parameter Range**: {issue['suggestion']} (Best value {issue['current_value']} is close to limit {issue['bound']})"
        )

    # 2. Correlation Recommendations
    if not correlations.empty:
        strong_pos = correlations[correlations > 0.3]
        strong_neg = correlations[correlations < -0.3]

        for param, corr in strong_pos.items():
            clean_param = param.replace("params_", "")
            recs.append(
                f"- **Trend**: `{clean_param}` shows strong positive correlation ({corr:.2f}) with performance. Consider shifting range higher."
            )

        for param, corr in strong_neg.items():
            clean_param = param.replace("params_", "")
            recs.append(
                f"- **Trend**: `{clean_param}` shows strong negative correlation ({corr:.2f}) with performance. Consider shifting range lower."
            )

    if not recs:
        recs.append("- No specific parameter adjustments suggested based on this data.")

    return recs


def main():
    args = parse_args()

    try:
        # Initialize optimizer
        optimizer = StrategyOptimizer()

        # Determine study name
        study_name = args.study_name
        if not study_name:
            try:
                study_names = optimizer.get_all_study_names()
                # Filter for pmm_dynamic studies if no specific name provided
                pmm_studies = [s for s in study_names if "pmm_dynamic" in s]
                if not pmm_studies:
                    # Fallback to any study if no pmm_dynamic ones
                    if study_names:
                        pmm_studies = study_names
                    else:
                        print("No studies found.")
                        return
                study_name = sorted(pmm_studies)[-1]
                print(f"Auto-selected latest study: {study_name}")
            except Exception as e:
                logger.error(f"Error fetching studies: {e}")
                return
        else:
            print(f"Analyzing requested study: {study_name}")

        # Load data
        try:
            study = optimizer.get_study(study_name)
            best_params = optimizer.get_study_best_params(study_name)
            trials_df = optimizer.get_study_trials_df(study_name)
        except Exception as e:
            logger.error(f"Failed to load study data: {e}")
            return

        # --- Analysis ---

        # 1. Correlations
        # Clean column names for correlation analysis
        clean_df = trials_df.copy()
        clean_df.columns = [c.replace("params_", "") for c in clean_df.columns]
        correlations = analyze_correlations(clean_df, target_col="value")

        # 2. Boundary Checks
        boundary_issues = check_boundaries(study, best_params)

        # 3. Generate Recommendations
        recommendations = generate_recommendations(correlations, boundary_issues)

        # --- Report Generation ---

        report = f"# Optimization Report: {study_name}\n\n"
        report += f"**Date:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        report += f"**Strategy:** PMM Dynamic\n\n"

        # Section 1: Executive Summary
        report += "## 1. Executive Summary\n\n"
        if "value" in trials_df.columns:
            best_score = trials_df["value"].max()
            avg_score = trials_df["value"].mean()
            report += f"- **Best Sharpe Ratio:** {best_score:.4f}\n"
            report += f"- **Average Sharpe Ratio:** {avg_score:.4f}\n"
            report += f"- **Total Trials:** {len(trials_df)}\n"

        report += "\n### Best Parameters\n"
        report += "```json\n"
        report += json.dumps(best_params, indent=2)
        report += "\n```\n\n"

        # Section 2: Analysis
        report += "## 2. Analysis\n\n"

        report += "### Parameter Correlations\n"
        report += "Correlation with Sharpe Ratio (Positive = Higher value leads to better score):\n\n"
        if not correlations.empty:
            corr_df = pd.DataFrame(correlations).reset_index()
            corr_df.columns = ["Parameter", "Correlation"]
            report += corr_df.to_markdown(index=False)
        else:
            report += "Not enough data for correlation analysis.\n"
        report += "\n\n"

        # Section 3: Recommendations
        report += "## 3. Next Steps & Recommendations\n\n"
        report += "Based on the optimization results, here are suggestions for the next iteration:\n\n"
        for rec in recommendations:
            report += f"{rec}\n"

        # Section 4: Top Trials
        report += "\n## 4. Top 5 Trials Detail\n\n"
        if "value" in trials_df.columns:
            completed = trials_df[trials_df["state"] == "COMPLETE"].copy()
            if not completed.empty:
                top_5 = completed.sort_values("value", ascending=False).head(5)

                # Clean columns
                display_cols = ["number", "value"] + [c for c in top_5.columns if c.startswith("params_")]
                display_df = top_5[display_cols].copy()
                display_df.columns = [c.replace("params_", "") for c in display_df.columns]

                report += display_df.to_markdown(index=False)

        # --- Save Report ---
        output_dir = project_root / args.output_dir
        output_dir.mkdir(parents=True, exist_ok=True)

        filename = f"report_{study_name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
        output_path = output_dir / filename

        with open(output_path, "w") as f:
            f.write(report)

        print(f"Analysis complete.")
        print(f"Report saved to: {output_path}")

    except Exception as e:
        logger.error(f"Error generating summary: {e}")
        import traceback

        traceback.print_exc()


if __name__ == "__main__":
    main()
