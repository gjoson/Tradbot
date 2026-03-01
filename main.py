"""
Tradbot: Production-Grade Automated Options Trading System
NIFTY Options | IST Timezone | Linux VPS | Systemd Compatible

Entry point and main orchestration logic.
Run with: python main.py
"""

import asyncio
import logging
import signal
import sys
from datetime import datetime
from pytz import timezone
import json

# Local imports
from config.settings import IST, LOG_LEVEL, LOG_DIR, DRY_RUN_MODE
from core.event_bus import get_event_bus, reset_event_bus, EventType
from core.login_manager import LoginManager
from core.websocket_client import WebSocketClient
from core.market_data import MarketData
from core.atm_strike_finder import ATMStrikeFinder
from core.market_classifier import MarketClassifier
from core.strategy_engine import StrategyEngine
from core.risk_manager import RiskManager
from core.order_manager import OrderManager
from core.broker_interface import BrokerInterface
from core.pnl_tracker import PnLTracker
from core.trade_logger import TradeLogger
from core.scheduler import Scheduler
from utils.time_utils import get_current_ist_time

# Setup logging
logging.basicConfig(
    level=LOG_LEVEL,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(f'{LOG_DIR}/tradbot.log'),
        logging.StreamHandler(sys.stdout)
    ]
)

logger = logging.getLogger(__name__)


class TradBotSystem:
    """
    Main trading system orchestrator.
    
    Coordinates all modules and manages the trading lifecycle.
    """
    
    def __init__(self):
        self._logger = logging.getLogger(f"{__name__}.TradBot")
        self._is_running = False
        self._is_shutdown = False
        
        # Module instances
        self.login_manager: LoginManager = None
        self.websocket_client: WebSocketClient = None
        self.market_data: MarketData = None
        self.atm_finder: ATMStrikeFinder = None
        self.market_classifier: MarketClassifier = None
        self.strategy_engine: StrategyEngine = None
        self.risk_manager: RiskManager = None
        self.order_manager: OrderManager = None
        self.broker_interface: BrokerInterface = None
        self.pnl_tracker: PnLTracker = None
        self.trade_logger: TradeLogger = None
        self.scheduler: Scheduler = None
        
        # State
        self.event_bus = None
    
    async def initialize(self) -> bool:
        """Initialize all system components."""
        try:
            self._logger.info("=" * 80)
            self._logger.info("TRADBOT System Initialization Starting")
            self._logger.info(f"Timestamp: {get_current_ist_time().isoformat()}")
            self._logger.info(f"Dry Run Mode: {DRY_RUN_MODE}")
            self._logger.info("=" * 80)
            
            # 1. Initialize event bus
            self.event_bus = await get_event_bus()
            self._logger.info("✓ Event bus initialized")
            
            # 2. Initialize login manager
            self.login_manager = LoginManager()
            if not await self.login_manager.login():
                self._logger.error("Failed to authenticate with broker")
                return False
            self._logger.info("✓ Login manager initialized")
            
            # 3. Initialize WebSocket client (but don't connect yet)
            self.websocket_client = WebSocketClient(self.login_manager)
            self._logger.info("✓ WebSocket client initialized")
            
            # 4. Initialize market data
            self.market_data = MarketData()
            self._logger.info("✓ Market data initialized")
            
            # 5. Initialize ATM finder
            self.atm_finder = ATMStrikeFinder()
            self._logger.info("✓ ATM strike finder initialized")
            
            # 6. Initialize market classifier
            self.market_classifier = MarketClassifier()
            self._logger.info("✓ Market classifier initialized")
            
            # 7. Initialize strategy engine
            self.strategy_engine = StrategyEngine(self.atm_finder)
            self._logger.info("✓ Strategy engine initialized")
            
            # 8. Initialize risk manager
            self.risk_manager = RiskManager()
            self.risk_manager.set_account_balance(100000.0)  # Default 1L, set from broker later
            self._logger.info("✓ Risk manager initialized")
            
            # 9. Initialize order manager
            self.order_manager = OrderManager()
            await self.order_manager.initialize()
            self._logger.info("✓ Order manager initialized")
            
            # 10. Initialize broker interface
            self.broker_interface = BrokerInterface(self.login_manager)
            self._logger.info("✓ Broker interface initialized")
            
            # 11. Initialize P&L tracker
            self.pnl_tracker = PnLTracker()
            self._logger.info("✓ P&L tracker initialized")
            
            # 12. Initialize trade logger
            self.trade_logger = TradeLogger()
            self._logger.info("✓ Trade logger initialized")

            # Inject dependencies into order manager
            self.order_manager.set_dependencies(self.trade_logger, self.broker_interface)

            # 13a. Perform startup reconciliation: rebuild state from broker
            await self._startup_reconcile()
            self._logger.info("✓ Startup reconciliation complete")
            
            # 13. Initialize scheduler
            self.scheduler = Scheduler()
            self._logger.info("✓ Scheduler initialized")
            
            # 14. Register event handlers
            await self._register_event_handlers()
            self._logger.info("✓ Event handlers registered")
            
            self._logger.info("=" * 80)
            self._logger.info("TRADBOT Initialization Complete")
            self._logger.info("=" * 80)
            
            return True
        
        except Exception as e:
            self._logger.error(f"Initialization failed: {e}", exc_info=True)
            return False
    
    async def _register_event_handlers(self) -> None:
        """Register event handlers for system events."""
        # Add handlers for critical events here
        bus = self.event_bus

        async def on_trading_halted(event):
            self._logger.critical(f"Trading halted: {event.data}")
            # Additional actions: cancel scheduled entries, persist state

        bus.subscribe(EventType.TRADING_HALTED, on_trading_halted)

        async def on_token_expired(event):
            self._logger.warning("Token expired event received")

        bus.subscribe(EventType.TOKEN_EXPIRED, on_token_expired)

        async def on_login_failed(event):
            self._logger.error("Login failed event received")

        bus.subscribe(EventType.LOGIN_FAILED, on_login_failed)

        return

    async def _startup_reconcile(self) -> None:
        """Fetch open positions and orders from broker and rebuild internal state."""
        try:
            # 1. Fetch open positions
            positions = await self.broker_interface.get_open_positions()
            if positions:
                for pos in positions:
                    try:
                        # Build a simple Position object and register
                        position_id = pos.get('position_id') or pos.get('id') or str(pos.get('instrument'))
                        entry_price = float(pos.get('avg_price', 0) or pos.get('entry_price', 0))
                        entry_time = datetime.now(IST)
                        legs = pos.get('legs', []) if isinstance(pos.get('legs', []), list) else []

                        from core.risk_manager import Position as RMPosition

                        p = RMPosition(
                            position_id=position_id,
                            entry_time=entry_time,
                            entry_price=entry_price,
                            legs=legs,
                            strategy=pos.get('strategy', 'reconciled'),
                            initial_capital_used=float(pos.get('initial_capital', 0) or 0)
                        )
                        await self.risk_manager.register_position(p)
                    except Exception as e:
                        self._logger.warning(f"Failed to reconstruct position: {e}")

            # 2. Fetch open orders and ensure idempotency records
            open_orders = await self.broker_interface.get_open_orders()
            if open_orders and self.trade_logger:
                for o in open_orders:
                    client_id = o.get('clientOrderId') or o.get('client_order_id')
                    broker_id = o.get('orderid') or o.get('order_id') or o.get('id')
                    order_data = o
                    try:
                        await self.trade_logger.record_order(client_id or f"broker_{broker_id}", broker_id, order_data)
                    except Exception:
                        self._logger.warning("Failed to record open order in DB")

            # 3. Prevent duplicate order placement by loading known client_order_ids
            # (OrderManager checks DB before placing orders)

            # 4. Load persisted state.json if exists
            await self._load_state()

        except Exception as e:
            self._logger.error(f"Startup reconciliation error: {e}")

    async def _save_state_atomic(self) -> None:
        """Atomically save runtime state to STATE_FILE_PATH using temp file + rename."""
        try:
            from config.settings import STATE_FILE_PATH

            state = {
                'open_positions': [p.position_id for p in self.risk_manager.open_positions.values()],
                'timestamp': datetime.now(IST).isoformat()
            }

            import tempfile, os, json

            d = os.path.dirname(STATE_FILE_PATH)
            if d and not os.path.exists(d):
                os.makedirs(d, exist_ok=True)

            fd, tmp_path = tempfile.mkstemp(dir=d)
            try:
                with os.fdopen(fd, 'w') as f:
                    json.dump(state, f)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp_path, STATE_FILE_PATH)
            finally:
                if os.path.exists(tmp_path):
                    try:
                        os.remove(tmp_path)
                    except Exception:
                        pass

        except Exception as e:
            self._logger.error(f"Error saving state: {e}")

    async def _load_state(self) -> None:
        """Load persisted state.json if available."""
        try:
            from config.settings import STATE_FILE_PATH
            import os, json

            if not os.path.exists(STATE_FILE_PATH):
                return

            with open(STATE_FILE_PATH, 'r') as f:
                state = json.load(f)
            self._logger.info(f"Loaded state: {state}")
        except Exception as e:
            self._logger.error(f"Error loading state: {e}")
    
    async def run(self) -> None:
        """Main event loop."""
        if not self._is_running:
            self._is_running = True
            self._logger.info("TradBot system starting...")
        
        try:
            # Create main tasks
            tasks = [
                asyncio.create_task(self.event_bus.process_events()),
                asyncio.create_task(self.scheduler.run_scheduler()),
                asyncio.create_task(self.order_manager.execute_orders()),
                # Add more tasks as needed
            ]
            
            # Wait for tasks
            await asyncio.gather(*tasks)
        
        except asyncio.CancelledError:
            self._logger.info("TradBot run cancelled")
        except Exception as e:
            self._logger.error(f"TradBot run error: {e}", exc_info=True)
        finally:
            await self.shutdown()
    
    async def shutdown(self) -> None:
        """Graceful shutdown of all components."""
        if self._is_shutdown:
            return
        
        self._is_shutdown = True
        self._logger.info("=" * 80)
        self._logger.info("TRADBOT Shutting Down")
        self._logger.info("=" * 80)
        
        try:
            # Close all positions
            if self.risk_manager.open_positions:
                self._logger.warning(
                    f"Closing {len(self.risk_manager.open_positions)} open positions"
                )
                for position_id in list(self.risk_manager.open_positions.keys()):
                    await self.risk_manager.close_position(
                        position_id, 0.0, "System shutdown"
                    )
            
            # Disconnect WebSocket
            if self.websocket_client.is_connected():
                await self.websocket_client.disconnect()
            
            # Logout
            if self.login_manager.is_authenticated():
                await self.login_manager.logout()
            
            # Close trade logger
            if self.trade_logger:
                self.trade_logger.close()
            
            # Shutdown event bus
            if self.event_bus:
                await self.event_bus.shutdown()
            
            self._logger.info("=" * 80)
            self._logger.info("TRADBOT Shutdown Complete")
            self._logger.info("=" * 80)
        
        except Exception as e:
            self._logger.error(f"Error during shutdown: {e}", exc_info=True)


# Global system instance
_system_instance: TradBotSystem = None


def get_system() -> TradBotSystem:
    """Get global system instance."""
    global _system_instance
    if _system_instance is None:
        _system_instance = TradBotSystem()
    return _system_instance


async def main():
    """Main entry point."""
    system = get_system()
    
    # Initialize
    if not await system.initialize():
        logger.error("System initialization failed")
        sys.exit(1)
    
    # Setup signal handlers
    def signal_handler(signum, frame):
        logger.info(f"Signal {signum} received, initiating shutdown...")
        # Schedule shutdown
        asyncio.create_task(system.shutdown())
    
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    # Run system
    try:
        await system.run()
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        await system.shutdown()
    except Exception as e:
        logger.error(f"Unexpected error: {e}", exc_info=True)
        await system.shutdown()
        sys.exit(1)


if __name__ == "__main__":
    # Suppress logs if running under systemd
    if sys.stdout.isatty():
        print("=" * 80)
        print("TRADBOT - Production Options Trading System")
        print("=" * 80)
    
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("TradBot terminated by user")
    except Exception as e:
        logger.critical(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)
