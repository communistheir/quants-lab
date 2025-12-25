import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Dict

import pandas as pd
from dotenv import load_dotenv
from hummingbot.strategy_v2.executors.position_executor.data_types import TrailingStop

from app.controllers.market_making.pmm_dynamic import PMMDynamicControllerConfig
from core.backtesting.optimizer import BacktestingConfig, BaseStrategyConfigGenerator, StrategyOptimizer
from core.tasks import BaseTask, TaskContext

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
load_dotenv()


class PMMDynamicConfigGenerator(BaseStrategyConfigGenerator):
    """
    Strategy configuration generator for PMM Dynamic market making optimization.

    Parameters to optimize:
    - Spread distances (buy_spreads, sell_spreads)
    - MACD parameters (fast, slow, signal)
    - NATR window length
    - Risk management (take_profit, stop_loss)
    """

    async def generate_config(self, trial) -> BacktestingConfig:
        # Suggest individual spread parameters instead of categorical dicts
        # This avoids the 'NoneType' issue with spread configuration
        buy_spread_1 = trial.suggest_float("buy_spread_1", 0.5, 1.5, step=0.25)
        buy_spread_2 = trial.suggest_float("buy_spread_2", 1.0, 2.5, step=0.25)
        buy_spread_3 = trial.suggest_float("buy_spread_3", 2.0, 4.0, step=0.5)

        sell_spread_1 = trial.suggest_float("sell_spread_1", 0.5, 1.5, step=0.25)
        sell_spread_2 = trial.suggest_float("sell_spread_2", 1.0, 2.5, step=0.25)
        # Updated: Decreased lower bound from 2.0 to 1.0 (best value 2.0 was close to limit)
        sell_spread_3 = trial.suggest_float("sell_spread_3", 1.0, 2.5, step=0.25)

        # Suggest MACD parameters (control price shift sensitivity)
        # Updated: Increased upper bound from 20 to 25 (best value 20 was close to limit)
        macd_fast = trial.suggest_int("macd_fast", 15, 25, step=2)
        # Updated: Decreased lower bound from 25 to 20 (best value 25 was close to limit)
        macd_slow = trial.suggest_int("macd_slow", 20, 45, step=5)
        # Updated: Decreased range from 5-15 to 3-11 (negative correlation -0.46)
        macd_signal = trial.suggest_int("macd_signal", 3, 11, step=2)

        # Suggest NATR parameters (control dynamic spread width)
        natr_length = trial.suggest_int("natr_length", 10, 30, step=5)

        # Suggest risk management parameters
        # Updated: Decreased range from 0.02-0.05 to 0.01-0.04 (negative correlation -0.36)
        take_profit = trial.suggest_float("take_profit", 0.01, 0.04, step=0.01)
        # Updated: Decreased lower bound from 0.01 to 0.005 (best value 0.01 was close to limit)
        stop_loss = trial.suggest_float("stop_loss", 0.005, 0.025, step=0.005)

        # Fixed parameters (spot trading, no leverage)
        total_amount_quote = 500  # 500 USDT
        leverage = 1  # Spot trading

        # Create the strategy configuration
        config = PMMDynamicControllerConfig(
            connector_name=self.config["connector_name"],
            trading_pair=self.config["trading_pair"],
            candles_connector=self.config["connector_name"],
            candles_trading_pair=self.config["trading_pair"],
            interval="3m",  # 3-minute candles for market making
            buy_spreads=[buy_spread_1, buy_spread_2, buy_spread_3],
            sell_spreads=[sell_spread_1, sell_spread_2, sell_spread_3],
            macd_fast=macd_fast,
            macd_slow=macd_slow,
            macd_signal=macd_signal,
            natr_length=natr_length,
            total_amount_quote=Decimal(total_amount_quote),
            take_profit=Decimal(take_profit),
            stop_loss=Decimal(stop_loss),
            leverage=Decimal(leverage),
            buy_amounts_pct=[Decimal("0.33"), Decimal("0.33"), Decimal("0.34")],  # Split 100% among 3 levels
            sell_amounts_pct=[Decimal("0.33"), Decimal("0.33"), Decimal("0.34")],  # Split 100% among 3 levels
        )

        # Return the configuration encapsulated in BacktestingConfig
        return BacktestingConfig(config=config, start=self.start, end=self.end)


class PMMDynamicBacktestingTask(BaseTask):
    """Backtesting task for PMM Dynamic market making strategy optimization."""

    def __init__(self, config):
        super().__init__(config)

        # Configuration with defaults
        task_config = self.config.config
        self.resolution = task_config.get("resolution", "3m")
        self.connector_name = task_config.get("connector_name", "binance")
        self.selected_pairs = task_config.get("selected_pairs", ["BTC-USDT"])
        self.lookback_days = task_config.get("lookback_days", 30)
        self.end_time_buffer_hours = task_config.get("end_time_buffer_hours", 6)
        self.n_trials = task_config.get("n_trials", 50)
        self.study_name_base = task_config.get("study_name", "pmm_dynamic_v1_task")

        # Initialize optimizer (will be set up in setup)
        self.optimizer = None

    async def setup(self, context: TaskContext) -> None:
        """Setup task before execution, including validation of prerequisites."""
        # Call parent setup to initialize database and notification services
        await super().setup(context)

        try:
            # Validate prerequisites
            if not self.connector_name:
                raise RuntimeError("connector_name not configured")
            if not self.selected_pairs:
                raise RuntimeError("selected_pairs not configured")

            # Initialize strategy optimizer (uses default DirectionalTradingBacktesting)
            # PMM Dynamic uses executors like directional strategies
            self.optimizer = StrategyOptimizer(resolution=self.resolution, load_cached_data=True)

            logging.info(f"Setup completed for {context.task_name}")
            logging.info(f"Connector: {self.connector_name} (Spot Trading)")
            logging.info(f"Resolution: {self.resolution}")
            logging.info(f"Trading pairs: {len(self.selected_pairs)} pairs")
            logging.info(f"Lookback days: {self.lookback_days}")
            logging.info(f"N trials: {self.n_trials}")
            logging.info(f"Optimization target: Sharpe Ratio")

        except Exception as e:
            logging.error(f"Setup failed: {e}")
            raise

    async def cleanup(self, context: TaskContext, result) -> None:
        """Cleanup after task execution."""
        try:
            logging.info(f"Cleanup completed for {context.task_name}")
        except Exception as e:
            logging.warning(f"Cleanup error: {e}")

    async def execute(self, context: TaskContext) -> Dict[str, Any]:
        """Main execution logic."""
        start_execution = datetime.now(timezone.utc)
        logging.info(f"Starting PMM Dynamic optimization for {len(self.selected_pairs)} pairs")

        try:
            # Track statistics
            stats = {
                "pairs_processed": 0,
                "pairs_total": len(self.selected_pairs),
                "optimizations_completed": 0,
                "total_trials": 0,
                "errors": 0,
            }

            today_str = datetime.now().strftime("%Y-%m-%d")
            generated_studies = []

            for trading_pair in self.selected_pairs:
                try:
                    # Calculate time range dynamically
                    import time

                    end_date = time.time() - (self.end_time_buffer_hours * 3600)
                    start_date = end_date - (self.lookback_days * 24 * 3600)

                    logging.info(f"Optimizing strategy for {self.connector_name} {trading_pair}")
                    logging.info(f"Time range: {pd.to_datetime(start_date, unit='s')} to {pd.to_datetime(end_date, unit='s')}")

                    # Create study name with date suffix
                    study_name = f"{self.study_name_base}_{trading_pair.replace('-', '_').lower()}_{today_str}"
                    generated_studies.append(study_name)

                    # Optimize the strategy
                    await self.optimizer.optimize(
                        study_name=study_name,
                        config_generator=PMMDynamicConfigGenerator(
                            start_date=datetime.fromtimestamp(start_date),
                            end_date=datetime.fromtimestamp(end_date),
                            config={"connector_name": self.connector_name, "trading_pair": trading_pair},
                        ),
                        n_trials=self.n_trials,
                    )

                    try:
                        best_params = self.optimizer.get_study_best_params(study_name)
                        logging.info(f"Best params for {trading_pair}: {best_params}")
                        stats["optimizations_completed"] += 1
                        stats["total_trials"] += self.n_trials
                    except ValueError:
                        logging.warning(f"No successful trials for {trading_pair}")

                    stats["pairs_processed"] += 1

                except Exception as e:
                    logging.error(f"Error optimizing {trading_pair}: {e}")
                    stats["errors"] += 1
                    stats["pairs_processed"] += 1

            # Log execution summary
            execution_duration = datetime.now(timezone.utc) - start_execution
            logging.info(f"Optimization completed in {execution_duration}")
            logging.info(f"Stats: {stats}")

            if generated_studies:
                logging.info("=" * 50)
                logging.info("OPTIMIZATION COMPLETED. GENERATED STUDIES:")
                for study in generated_studies:
                    logging.info(f"  - {study}")
                logging.info("=" * 50)
                logging.info("To analyze results, run:")
                for study in generated_studies:
                    logging.info(f"  python scripts/summarize_optimization.py --study_name {study}")
                logging.info("=" * 50)

            return {
                "status": "completed",
                "stats": stats,
                "duration_seconds": execution_duration.total_seconds(),
                "generated_studies": generated_studies,
            }

        except Exception as e:
            logging.error(f"Execution failed: {e}")
            raise


async def main():
    """Standalone execution for testing."""
    # This allows running the task standalone for debugging
    from core.tasks import TaskContext

    config = {
        "config": {
            "connector_name": "binance",
            "selected_pairs": ["BTC-USDT"],
            "n_trials": 10,  # Small number for testing
            "lookback_days": 7,
            "end_time_buffer_hours": 6,
        }
    }

    task = PMMDynamicBacktestingTask(config)
    context = TaskContext(task_name="pmm_dynamic_backtest_test", task_id="test_123")

    await task.setup(context)
    result = await task.execute(context)
    print(f"Result: {result}")


if __name__ == "__main__":
    asyncio.run(main())
