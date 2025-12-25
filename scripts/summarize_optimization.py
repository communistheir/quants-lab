import sys
import os
from pathlib import Path
import pandas as pd
import logging
import json
import argparse
import numpy as np
from datetime import datetime
import os
import plotly.graph_objects as go
import plotly.express as px

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
    parser.add_argument("--figures_dir", type=str, default="backtesting_result/figures", help="Directory to save figures.")
    parser.add_argument("--format", type=str, choices=["html", "md", "both"], default="html", help="Output format for the report.")
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


def generate_recommendations(correlations, boundary_issues, best_trial=None):
    """Generate actionable recommendations based on analysis."""
    recs = []
    
    # 优化方向说明
    recs.append("<h3>📋 优化方向建议</h3>")
    
    # 1. Boundary Recommendations
    if boundary_issues:
        recs.append("<h4>1️⃣ 参数边界调整</h4>")
        recs.append("<p><i>以下参数的最优值接近搜索边界,建议扩大范围以探索更优解:</i></p>")
        recs.append("<ul>")
        for issue in boundary_issues:
            recs.append(
                f"<li><b>{issue['param']}</b>: {issue['suggestion']} (当前最优值 {issue['current_value']:.4f} 接近边界 {issue['bound']:.4f})</li>"
            )
        recs.append("</ul>")

    # 2. Correlation Recommendations
    if not correlations.empty:
        strong_pos = correlations[correlations > 0.3]
        strong_neg = correlations[correlations < -0.3]
        
        if not strong_pos.empty or not strong_neg.empty:
            recs.append("<h4>2️⃣ 参数相关性指导</h4>")
            recs.append("<p><i>根据参数与夏普比率的相关性,建议调整搜索范围:</i></p>")
            recs.append("<ul>")
            
            for param, corr in strong_pos.items():
                clean_param = param.replace("params_", "")
                recs.append(
                    f"<li><b>{clean_param}</b> 正相关性强 ({corr:.2f}) → 建议提高参数范围上限</li>"
                )

            for param, corr in strong_neg.items():
                clean_param = param.replace("params_", "")
                recs.append(
                    f"<li><b>{clean_param}</b> 负相关性强 ({corr:.2f}) → 建议降低参数范围上限</li>"
                )
            recs.append("</ul>")
    
    if len(recs) == 0:
        recs.append("<p>暂无具体优化建议</p>")

    return recs


def generate_best_params_summary(best_trial):
    """Generate HTML summary for best trial parameters and performance."""
    lines = []
    
    if best_trial is None:
        lines.append("<p>未找到最优试验结果</p>")
        return lines
    
    lines.append("<h3>🎯 最优参数配置</h3>")
    lines.append("<p><i>Trial #{} 的参数组合:</i></p>".format(best_trial.number if hasattr(best_trial, 'number') else 'N/A'))
    lines.append("<table border='1' style='border-collapse: collapse; width: 100%;'>")
    lines.append("<thead><tr><th>参数名称</th><th>值</th><th>说明</th></tr></thead>")
    lines.append("<tbody>")
    
    if hasattr(best_trial, 'params'):
        param_descriptions = {
            'buy_spread_1': '买单价差层级1',
            'buy_spread_2': '买单价差层级2', 
            'buy_spread_3': '买单价差层级3',
            'sell_spread_1': '卖单价差层级1',
            'sell_spread_2': '卖单价差层级2',
            'sell_spread_3': '卖单价差层级3',
            'macd_fast': 'MACD快线周期',
            'macd_slow': 'MACD慢线周期',
            'macd_signal': 'MACD信号线周期',
            'natr_length': 'NATR周期长度',
            'stop_loss': '止损比例',
            'take_profit': '止盈比例'
        }
        
        for param_name, param_value in sorted(best_trial.params.items()):
            display_name = param_descriptions.get(param_name, param_name)
            if isinstance(param_value, float):
                value_str = f"{param_value:.4f}"
            else:
                value_str = str(param_value)
            lines.append(f"<tr><td><b>{param_name}</b></td><td>{value_str}</td><td>{display_name}</td></tr>")
    
    lines.append("</tbody></table>")
    
    # 添加性能指标
    if hasattr(best_trial, 'user_attrs'):
        lines.append("<h3>📊 回测表现</h3>")
        lines.append("<p><i>该参数组合的历史回测结果:</i></p>")
        lines.append("<ul>")
        sharpe = best_trial.value if hasattr(best_trial, 'value') else None
        if isinstance(sharpe, (int, float)):
            lines.append(f"<li><b>夏普比率:</b> {sharpe:.4f}</li>")
        else:
            lines.append(f"<li><b>夏普比率:</b> {sharpe}</li>")
        
        attrs_to_show = {
            'net_pnl_quote': ('净盈亏', lambda x: f"${x:.2f}"),
            'max_drawdown_pct': ('最大回撤', lambda x: f"{abs(x*100):.2f}%"),
            'accuracy': ('胜率', lambda x: f"{x*100:.1f}%"),
            'total_positions': ('总交易次数', lambda x: f"{int(x)}笔"),
            'profit_factor': ('盈亏比', lambda x: f"{x:.2f}")
        }
        
        for attr_key, (label, formatter) in attrs_to_show.items():
            if attr_key in best_trial.user_attrs:
                value = best_trial.user_attrs[attr_key]
                lines.append(f"<li><b>{label}:</b> {formatter(value)}</li>")
        lines.append("</ul>")
    
    return lines


def generate_quality_assessment(best_metrics: dict, trials_count: int) -> str:
    """Generate quality assessment summary for the best trial."""
    sharpe = best_metrics.get('sharpe_ratio', 0)
    net_pnl = best_metrics.get('net_pnl', 0)
    max_dd = abs(best_metrics.get('max_drawdown_pct', 0))
    win_rate = best_metrics.get('win_rate', 0)
    total_positions = best_metrics.get('total_positions', 0)
    
    # Quality indicators
    quality_points = []
    production_ready = True
    
    # Sharpe ratio assessment
    if sharpe >= 1.5:
        quality_points.append("✅ **夏普比率优秀** (≥1.5)，风险调整后收益表现突出")
    elif sharpe >= 1.0:
        quality_points.append("✓ **夏普比率良好** (≥1.0)，具备较好的风险收益比")
    elif sharpe >= 0.5:
        quality_points.append("⚠ **夏普比率一般** (0.5-1.0)，建议继续优化参数")
        production_ready = False
    else:
        quality_points.append("❌ **夏普比率偏低** (<0.5)，策略表现不稳定，不建议上线")
        production_ready = False
    
    # PnL assessment
    if net_pnl > 0:
        quality_points.append(f"✅ **盈利能力** 实现正收益 ${net_pnl:.2f}")
    else:
        quality_points.append(f"❌ **亏损状态** 净亏损 ${abs(net_pnl):.2f}，需调整策略")
        production_ready = False
    
    # Drawdown assessment
    if max_dd < 10:
        quality_points.append(f"✅ **回撤控制优秀** ({max_dd:.1f}%)，风险可控")
    elif max_dd < 20:
        quality_points.append(f"✓ **回撤可接受** ({max_dd:.1f}%)，建议加强风控")
    else:
        quality_points.append(f"❌ **回撤过大** ({max_dd:.1f}%)，风险较高，需优化止损策略")
        production_ready = False
    
    # Win rate assessment
    if win_rate >= 60:
        quality_points.append(f"✅ **胜率优秀** ({win_rate:.1f}%)，策略稳定性高")
    elif win_rate >= 50:
        quality_points.append(f"✓ **胜率良好** ({win_rate:.1f}%)，整体表现稳健")
    elif win_rate > 0:
        quality_points.append(f"⚠ **胜率偏低** ({win_rate:.1f}%)，建议优化入场条件")
    
    # Sample size assessment
    if total_positions < 30:
        quality_points.append(f"⚠ **样本量不足** ({int(total_positions)}笔交易)，建议延长回测周期以验证稳定性")
        production_ready = False
    elif total_positions >= 100:
        quality_points.append(f"✅ **样本充足** ({int(total_positions)}笔交易)，统计显著性较高")
    else:
        quality_points.append(f"✓ **样本量适中** ({int(total_positions)}笔交易)")
    
    # Optimization trials assessment
    if trials_count < 20:
        quality_points.append(f"⚠ **优化试验较少** ({trials_count}次)，建议增加试验次数以探索更优参数空间")
    
    # Final verdict
    summary = "### 📊 策略质量评估\n\n"
    summary += "\n".join(quality_points)
    summary += "\n\n"
    
    if production_ready:
        summary += "**✅ 生产就绪度评估：当前策略已达到基本上线标准**  \n"
        summary += "*建议：在模拟盘环境验证1-2周后，可考虑小仓位实盘测试。持续监控实盘表现与回测偏差。*\n"
    else:
        summary += "**❌ 生产就绪度评估：当前策略尚未达到上线标准**  \n"
        summary += "*建议：根据上述问题点优化参数范围、增加试验次数、延长回测周期或调整策略逻辑后重新评估。*\n"
    
    return summary


def save_plot(fig, path: Path):
    """Save Plotly figure as standalone HTML file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.write_html(str(path), include_plotlyjs='cdn')


def get_plot_html_div(fig) -> str:
    """Get Plotly figure as HTML div (for embedding)."""
    if fig is None:
        return "<p><i>Chart not available</i></p>"
    # Use include_mathjax=False to prevent binary data encoding which may not work in all browsers
    return fig.to_html(
        include_plotlyjs=False, 
        full_html=False, 
        div_id=None, 
        config={'responsive': True},
        include_mathjax=False,
        # Force JSON encoding instead of binary
        post_script=None
    )


def relpath_for_embed(target: Path, base: Path) -> str:
    try:
        return os.path.relpath(target, base)
    except ValueError:
        return str(target)


def plot_value_distribution(trials_df: pd.DataFrame, out_path: Path):
    if "value" not in trials_df.columns:
        return None
    
    # Filter for completed trials with valid values
    valid_trials = trials_df[trials_df["value"].notna()].copy()
    
    if valid_trials.empty:
        return None
    
    from plotly.subplots import make_subplots
    
    fig = make_subplots(rows=1, cols=2, subplot_titles=("Sharpe Distribution", "Sharpe Boxplot"))
    
    # Histogram - convert to list to avoid binary encoding in HTML
    fig.add_trace(
        go.Histogram(x=valid_trials["value"].tolist(), nbinsx=15, marker_color="#4c78a8", name="Distribution"),
        row=1, col=1
    )
    
    # Boxplot - convert to list to avoid binary encoding in HTML
    fig.add_trace(
        go.Box(y=valid_trials["value"].tolist(), marker_color="#4c78a8", name="Boxplot"),
        row=1, col=2
    )
    
    fig.update_xaxes(title_text="Sharpe Ratio", row=1, col=1)
    fig.update_yaxes(title_text="Count", row=1, col=1)
    fig.update_yaxes(title_text="Sharpe Ratio", row=1, col=2)
    fig.update_layout(showlegend=False, height=400, width=800)
    
    save_plot(fig, out_path)
    return fig


def plot_param_importance(study, out_path: Path):
    try:
        from optuna.importance import get_param_importances

        importances = get_param_importances(study)
        if not importances:
            return None
        
        names = list(importances.keys())
        vals = list(importances.values())
        
        # Convert to list to avoid binary encoding in HTML
        fig = go.Figure(go.Bar(
            x=vals.tolist() if hasattr(vals, 'tolist') else list(vals),
            y=names.tolist() if hasattr(names, 'tolist') else list(names),
            orientation='h',
            marker_color="#f58518"
        ))
        fig.update_layout(
            title="Parameter Importance",
            xaxis_title="Importance",
            yaxis_title="",
            height=max(300, len(names) * 30),
            width=800
        )
        
        save_plot(fig, out_path)
        return fig
    except Exception as e:
        logger.warning(f"Failed to compute param importances: {e}")
        return None


def plot_correlations(correlations: pd.Series, out_path: Path):
    if correlations.empty:
        return None
    
    sorted_corr = correlations.sort_values()
    
    # Convert to list to avoid binary encoding in HTML
    fig = go.Figure(go.Bar(
        x=sorted_corr.values.tolist(),
        y=sorted_corr.index.tolist(),
        orientation='h',
        marker_color="#54a24b"
    ))
    fig.update_layout(
        title="Correlation with Sharpe Ratio",
        xaxis_title="Correlation",
        yaxis_title="",
        height=max(300, len(sorted_corr) * 30),
        width=800
    )
    
    save_plot(fig, out_path)
    return fig


def main():
    args = parse_args()

    try:
        # Initialize optimizer
        optimizer = StrategyOptimizer()

        # Determine study name
        study_name = args.study_name
        if not study_name:
            study_names = optimizer.get_all_study_names()
            pmm_studies = [s for s in study_names if "pmm_dynamic" in s]
            if not pmm_studies:
                if not study_names:
                    print("No studies found.")
                    return
                pmm_studies = study_names
            pmm_studies.sort()
            study_name = pmm_studies[-1]

        # Load study and dataframe
        study = optimizer.get_study(study_name)
        trials_df = optimizer.get_study_trials_df(study_name)
        if trials_df.empty:
            print("No trials found in study.")
            return

        # Best params and metrics
        best_params = getattr(study, "best_trial", None)
        best_params = best_params.params if best_params else {}
        
        # Extract best trial metrics (from user_attrs)
        best_trial = study.best_trial if hasattr(study, 'best_trial') else None
        best_metrics = {}
        if best_trial and hasattr(best_trial, 'user_attrs'):
            # accuracy field is already a percentage (0-1 range)
            accuracy = best_trial.user_attrs.get('accuracy', 0)
            
            # max_drawdown_pct is already negative, multiply by 100 to convert to percentage display
            max_dd_pct = best_trial.user_attrs.get('max_drawdown_pct', 0)
            
            best_metrics = {
                'net_pnl': best_trial.user_attrs.get('net_pnl_quote', best_trial.user_attrs.get('net_pnl', 0)),
                'max_drawdown_pct': abs(max_dd_pct * 100),  # Convert to positive percentage
                'max_drawdown_usd': abs(best_trial.user_attrs.get('max_drawdown_usd', 0)),
                'total_positions': best_trial.user_attrs.get('total_positions', 0),
                'total_executors': best_trial.user_attrs.get('total_executors', 0),
                'sharpe_ratio': best_trial.user_attrs.get('sharpe_ratio', best_trial.value),
                'win_rate': accuracy * 100,  # Convert decimal to percentage
                'profit_factor': best_trial.user_attrs.get('profit_factor', 0),
                'total_volume': best_trial.user_attrs.get('total_volume', 0),
            }
        
        # Calculate average daily trades from backtest duration (not trial execution time)
        if best_trial and best_metrics.get('total_positions', 0) > 0:
            try:
                # Try to get actual backtest duration from user_attrs if available
                # This should be the time span of the historical data, not the trial execution time
                backtest_days = None
                
                # Method 1: Try to get from user_attrs (if the backtesting task stores it)
                if hasattr(best_trial, 'user_attrs'):
                    backtest_days = best_trial.user_attrs.get('backtest_days') or best_trial.user_attrs.get('lookback_days')
                
                # Method 2: Fallback - estimate from study name or use default
                # Most backtests use 7 days lookback by default
                if backtest_days is None:
                    backtest_days = 7  # Default assumption
                
                best_metrics['avg_daily_trades'] = best_metrics['total_positions'] / backtest_days
            except Exception as e:
                best_metrics['avg_daily_trades'] = None

        # Analyses
        correlations = analyze_correlations(trials_df)
        boundary_issues = check_boundaries(study, best_params)
        best_trial = study.best_trial if hasattr(study, 'best_trial') else None
        recommendations = generate_recommendations(correlations, boundary_issues, best_trial)
        quality_assessment = generate_quality_assessment(best_metrics, len(trials_df))

        # Figures (now return Plotly figure objects, not paths)
        figures_dir = project_root / args.figures_dir
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        fig_objects = {}

        hist_path = figures_dir / f"{study_name}_sharpe_dist_{timestamp}.html"
        fig_objects["sharpe_dist"] = plot_value_distribution(trials_df, hist_path)

        imp_path = figures_dir / f"{study_name}_importance_{timestamp}.html"
        fig_objects["importance"] = plot_param_importance(study, imp_path)

        corr_path = figures_dir / f"{study_name}_correlation_{timestamp}.html"
        fig_objects["correlation"] = plot_correlations(correlations, corr_path)

        # Report paths
        output_dir = project_root / args.output_dir
        output_dir.mkdir(parents=True, exist_ok=True)
        base_filename = f"report_{study_name}_{timestamp}"


        # HTML
        if args.format in ("html", "both"):
            html_lines = []
            html_lines.append("<!DOCTYPE html>")
            html_lines.append("<html><head>")
            html_lines.append("<meta charset='utf-8'>")
            html_lines.append("<title>Optimization Report</title>")
            html_lines.append("<script src='https://cdn.plot.ly/plotly-2.27.0.min.js' charset='utf-8'></script>")
            html_lines.append("<style>body { font-family: Arial, sans-serif; margin: 20px; } .plotly-graph-div { margin: 20px 0; }</style>")
            html_lines.append("</head><body>")
            html_lines.append(f"<h1>Optimization Report: {study_name}</h1>")
            html_lines.append(f"<p><b>Date:</b> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}<br>")
            html_lines.append(f"<b>Strategy:</b> PMM Dynamic</p>")

            # Exec summary
            html_lines.append("<h2>1. Executive Summary</h2>")
            
            # Use best params summary to replace old executive summary
            best_trial = study.best_trial if hasattr(study, 'best_trial') else None
            best_params_html = generate_best_params_summary(best_trial)
            for line in best_params_html:
                html_lines.append(line)
            
            # Quality assessment
            if best_metrics:
                html_lines.append(quality_assessment.replace("\n", "<br>").replace("**", "<b>").replace("**", "</b>").replace("*", "<i>").replace("*", "</i>"))

            # Analysis with interactive plots
            html_lines.append("<h2>2. Analysis</h2>")
            if fig_objects.get("sharpe_dist"):
                html_lines.append("<h3>Sharpe Distribution</h3>")
                html_lines.append("<p><i>展示所有优化试验的夏普比率分布情况。左图（直方图）显示夏普比率的频率分布，可以看出大部分试验的表现集中在哪个区间；右图（箱线图）展示中位数、四分位数和异常值，帮助识别整体表现的稳定性。数值越高表示风险调整后的收益越好。</i></p>")
                html_lines.append(get_plot_html_div(fig_objects["sharpe_dist"]))
            if fig_objects.get("importance"):
                html_lines.append("<h3>Parameter Importance</h3>")
                html_lines.append("<p><i>显示各参数对优化目标（夏普比率）的影响程度。数值越大表示该参数对策略表现的影响越显著。这可以帮助您识别最关键的参数，在后续优化中重点调整这些参数可能带来更大的改进空间。</i></p>")
                html_lines.append(get_plot_html_div(fig_objects["importance"]))
            if fig_objects.get("correlation"):
                html_lines.append("<h3>Correlation with Sharpe Ratio</h3>")
                html_lines.append("<p><i>展示各参数与夏普比率的相关性。正值（绿色靠右）表示参数增大时夏普比率倾向于提高；负值（绿色靠左）表示参数减小时表现更好。相关性绝对值越大，该参数与策略表现的线性关系越强。建议：对正相关参数考虑提高范围上限，对负相关参数考虑降低范围上限。</i></p>")
                html_lines.append(get_plot_html_div(fig_objects["correlation"]))

            # Recommendations
            html_lines.append("<h2>3. Next Steps & Recommendations</h2>")
            # Recommendations now return HTML fragments, not list items
            for rec in recommendations:
                html_lines.append(rec)

            # Top trials
            html_lines.append("<h2>4. Top 5 Trials Detail</h2>")
            if "value" in trials_df.columns:
                completed = trials_df[trials_df["state"] == "COMPLETE"].copy()
                if not completed.empty:
                    top_5 = completed.sort_values("value", ascending=False).head(5)
                    display_cols = ["number", "value"] + [c for c in top_5.columns if c.startswith("params_")]
                    display_df = top_5[display_cols].copy()
                    display_df.columns = [c.replace("params_", "") for c in display_df.columns]
                    html_lines.append(display_df.to_html(index=False))

            html_lines.append("</body></html>")
            html_output = output_dir / f"{base_filename}.html"
            with open(html_output, "w") as f:
                f.write("\n".join(html_lines))
            print(f"HTML report saved to: {html_output}")

        # Markdown (optional)
        if args.format in ("md", "both"):
            report = f"# Optimization Report: {study_name}\n\n"
            report += f"**Date:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            report += f"**Strategy:** PMM Dynamic\n\n"

            report += "## 1. Executive Summary\n\n"
            if "value" in trials_df.columns:
                best_score = trials_df["value"].max()
                avg_score = trials_df["value"].mean()
                report += "### Performance Metrics\n\n"
                report += f"- **Best Sharpe Ratio:** {best_score:.4f}\n"
                report += f"- **Average Sharpe Ratio:** {avg_score:.4f}\n"
                report += f"- **Total Trials:** {len(trials_df)}\n\n"
                
                # Add business metrics
                if best_metrics:
                    report += "### Best Trial Business Metrics\n\n"
                    if best_metrics.get('net_pnl') is not None:
                        report += f"- **Net PnL:** ${best_metrics['net_pnl']:.2f} USDT\n"
                    if best_metrics.get('max_drawdown_pct') is not None:
                        report += f"- **Max Drawdown:** {best_metrics['max_drawdown_pct']:.2f}%\n"
                    if best_metrics.get('total_positions') is not None:
                        report += f"- **Total Positions:** {int(best_metrics['total_positions'])}\n"
                    if best_metrics.get('avg_daily_trades') is not None and best_metrics['avg_daily_trades'] > 0:
                        report += f"- **Avg Daily Positions:** {best_metrics['avg_daily_trades']:.1f}\n"
                    if best_metrics.get('win_rate') is not None:
                        report += f"- **Win Rate:** {best_metrics['win_rate']:.1f}%\n"
                    if best_metrics.get('profit_factor') is not None and best_metrics['profit_factor'] > 0:
                        report += f"- **Profit Factor:** {best_metrics['profit_factor']:.2f}\n"
                    report += "\n"

            report += "\n### Best Parameters\n"
            report += "```json\n" + json.dumps(best_params, indent=2) + "\n```\n\n"
            
            # Quality assessment
            if best_metrics:
                report += quality_assessment + "\n"

            report += "## 2. Analysis\n\n"
            
            if hist_path.exists():
                report += "### Sharpe Distribution\n\n"
                report += "*展示所有优化试验的夏普比率分布情况。左图（直方图）显示夏普比率的频率分布，可以看出大部分试验的表现集中在哪个区间；右图（箱线图）展示中位数、四分位数和异常值，帮助识别整体表现的稳定性。数值越高表示风险调整后的收益越好。*\n\n"
                report += f"- [查看交互图表]({relpath_for_embed(hist_path, output_dir)})\n\n"
                
            if imp_path.exists():
                report += "### Parameter Importance\n\n"
                report += "*显示各参数对优化目标（夏普比率）的影响程度。数值越大表示该参数对策略表现的影响越显著。这可以帮助您识别最关键的参数，在后续优化中重点调整这些参数可能带来更大的改进空间。*\n\n"
                report += f"- [查看交互图表]({relpath_for_embed(imp_path, output_dir)})\n\n"
                
            if corr_path.exists():
                report += "### Correlation with Sharpe Ratio\n\n"
                report += "*展示各参数与夏普比率的相关性。正值（绿色靠右）表示参数增大时夏普比率倾向于提高；负值（绿色靠左）表示参数减小时表现更好。相关性绝对值越大，该参数与策略表现的线性关系越强。建议：对正相关参数考虑提高范围上限，对负相关参数考虑降低范围上限。*\n\n"
                report += f"- [查看交互图表]({relpath_for_embed(corr_path, output_dir)})\n\n"
            
            if not (hist_path.exists() or imp_path.exists() or corr_path.exists()):
                report += "Interactive charts are available in the HTML report.\n\n"

            report += "## 3. Next Steps & Recommendations\n\n"
            for rec in recommendations:
                report += f"- {rec}\n"

            report += "\n## 4. Top 5 Trials Detail\n\n"
            if "value" in trials_df.columns:
                completed = trials_df[trials_df["state"] == "COMPLETE"].copy()
                if not completed.empty:
                    top_5 = completed.sort_values("value", ascending=False).head(5)
                    display_cols = ["number", "value"] + [c for c in top_5.columns if c.startswith("params_")]
                    display_df = top_5[display_cols].copy()
                    display_df.columns = [c.replace("params_", "") for c in display_df.columns]
                    report += display_df.to_markdown(index=False)

            md_output = output_dir / f"{base_filename}.md"
            with open(md_output, "w") as f:
                f.write(report)
            print(f"Markdown report saved to: {md_output}")

        print("Analysis complete.")

    except Exception as e:
        logger.error(f"Error generating summary: {e}")
        import traceback

        traceback.print_exc()


if __name__ == "__main__":
    main()
