"""
Trade Logger: Persistent logging of all trades to database.
Enables post-trade analysis and regulatory compliance.
"""

import logging
import sqlite3
import json
from datetime import datetime
from typing import Optional, Dict, List
from pytz import timezone

from config.settings import LOG_DB_PATH, LOG_TRADE_SCHEMA

logger = logging.getLogger(__name__)
IST = timezone('Asia/Kolkata')


class TradeLogger:
    """
    Logs all trades to SQLite database.
    
    Per-trade data:
    - Entry/exit time and price
    - Strategy and market conditions
    - P&L and exit reason
    - Order IDs and Greeks
    """
    
    def __init__(self, db_path: str = LOG_DB_PATH):
        self.db_path = db_path
        self._logger = logging.getLogger(f"{__name__}.TradeLogger")
        self._connection: Optional[sqlite3.Connection] = None
        
        # Initialize database
        self._init_db()
    
    def _init_db(self) -> None:
        """Initialize SQLite database and tables."""
        try:
            self._connection = sqlite3.connect(self.db_path)
            cursor = self._connection.cursor()
            
            # Create trades table
            create_table_sql = """
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL,
                time TEXT NOT NULL,
                strategy TEXT,
                market_regime TEXT,
                vix REAL,
                strikes TEXT,
                entry_time TEXT,
                entry_price REAL,
                exit_time TEXT,
                exit_price REAL,
                pnl REAL,
                pnl_percent REAL,
                max_drawdown REAL,
                exit_reason TEXT,
                order_ids TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """
            
            cursor.execute(create_table_sql)

            # Create orders table for idempotency
            create_orders_sql = """
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                client_order_id TEXT NOT NULL UNIQUE,
                broker_order_id TEXT,
                order_data TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """
            cursor.execute(create_orders_sql)
            
            # Create no-trade days table
            create_notrade_sql = """
            CREATE TABLE IF NOT EXISTS no_trade_days (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL UNIQUE,
                reason TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """
            
            cursor.execute(create_notrade_sql)
            
            self._connection.commit()
            self._logger.info(f"Database initialized at {self.db_path}")
        
        except Exception as e:
            self._logger.error(f"Database initialization error: {e}")
            raise
    
    async def log_trade(
        self,
        date: str,  # YYYY-MM-DD
        strategy: str,
        market_regime: str,
        vix: float,
        strikes: List[float],
        entry_time: str,
        entry_price: float,
        exit_time: str,
        exit_price: float,
        pnl: float,
        pnl_percent: float,
        max_drawdown: float,
        exit_reason: str,
        order_ids: List[str] = None
    ) -> Optional[int]:
        """
        Log a completed trade.
        
        Args:
            date: Trade date (YYYY-MM-DD)
            strategy: Strategy name
            market_regime: Market regime (bull/bear/range)
            vix: VIX at entry
            strikes: List of strikes involved
            entry_time: Entry time (HH:MM:SS)
            entry_price: Entry price
            exit_time: Exit time (HH:MM:SS)
            exit_price: Exit price
            pnl: Profit/loss amount
            pnl_percent: Profit/loss percentage
            max_drawdown: Max drawdown during position
            exit_reason: Reason for exit (SL/target/trailing/forced)
            order_ids: List of order IDs
        
        Returns:
            Row ID if successful, None if error
        """
        try:
            cursor = self._connection.cursor()
            
            insert_sql = """
            INSERT INTO trades (
                date, time, strategy, market_regime, vix, strikes,
                entry_time, entry_price, exit_time, exit_price,
                pnl, pnl_percent, max_drawdown, exit_reason, order_ids
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """
            
            time_str = datetime.now(IST).strftime('%H:%M:%S')
            
            cursor.execute(insert_sql, (
                date,
                time_str,
                strategy,
                market_regime,
                vix,
                json.dumps([str(s) for s in strikes]),
                entry_time,
                entry_price,
                exit_time,
                exit_price,
                pnl,
                pnl_percent,
                max_drawdown,
                exit_reason,
                json.dumps(order_ids or [])
            ))
            
            self._connection.commit()
            row_id = cursor.lastrowid
            
            self._logger.info(
                f"Trade logged (ID={row_id}): {strategy} | "
                f"Entry={entry_price:.2f}, Exit={exit_price:.2f}, "
                f"PnL={pnl:.2f} ({pnl_percent:.2f}%)"
            )
            
            return row_id
        
        except Exception as e:
            self._logger.error(f"Error logging trade: {e}")
            return None

    async def record_order(self, client_order_id: str, broker_order_id: str = None, order_data: dict = None) -> bool:
        """Record an order for idempotency checks. Uses transaction."""
        try:
            cursor = self._connection.cursor()
            insert_sql = """
            INSERT OR IGNORE INTO orders (client_order_id, broker_order_id, order_data)
            VALUES (?, ?, ?)
            """
            cursor.execute(insert_sql, (client_order_id, broker_order_id, json.dumps(order_data or {})))
            self._connection.commit()
            return True
        except Exception as e:
            self._logger.error(f"Error recording order: {e}")
            return False

    async def get_order_by_client_id(self, client_order_id: str) -> Optional[Dict]:
        """Retrieve order record by client_order_id."""
        try:
            cursor = self._connection.cursor()
            cursor.execute("SELECT * FROM orders WHERE client_order_id = ?", (client_order_id,))
            row = cursor.fetchone()
            if not row:
                return None
            columns = [desc[0] for desc in cursor.description]
            return dict(zip(columns, row))
        except Exception as e:
            self._logger.error(f"Error fetching order by client id: {e}")
            return None
    
    async def log_no_trade_day(self, date: str, reason: str) -> bool:
        """
        Log a day when no trades were executed.
        
        Args:
            date: Date (YYYY-MM-DD)
            reason: Reason (e.g., "No valid classification", "Event day")
        
        Returns:
            True if successful
        """
        try:
            cursor = self._connection.cursor()
            
            insert_sql = """
            INSERT OR IGNORE INTO no_trade_days (date, reason)
            VALUES (?, ?)
            """
            
            cursor.execute(insert_sql, (date, reason))
            self._connection.commit()
            
            self._logger.info(f"No-trade day logged: {date} ({reason})")
            
            return True
        
        except Exception as e:
            self._logger.error(f"Error logging no-trade day: {e}")
            return False
    
    async def get_trades(
        self,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        limit: int = 100
    ) -> List[Dict]:
        """
        Retrieve trades from database.
        
        Args:
            date_from: Start date (YYYY-MM-DD)
            date_to: End date (YYYY-MM-DD)
            limit: Max rows to return
        
        Returns:
            List of trade dicts
        """
        try:
            cursor = self._connection.cursor()
            
            query = "SELECT * FROM trades"
            params = []
            
            if date_from or date_to:
                query += " WHERE"
                if date_from:
                    query += " date >= ?"
                    params.append(date_from)
                    if date_to:
                        query += " AND"
                if date_to:
                    query += " date <= ?"
                    params.append(date_to)
            
            query += " ORDER BY date DESC, entry_time DESC LIMIT ?"
            params.append(limit)
            
            cursor.execute(query, params)
            rows = cursor.fetchall()
            
            # Convert to list of dicts
            columns = [desc[0] for desc in cursor.description]
            trades = [dict(zip(columns, row)) for row in rows]
            
            self._logger.debug(f"Retrieved {len(trades)} trades")
            
            return trades
        
        except Exception as e:
            self._logger.error(f"Error retrieving trades: {e}")
            return []
    
    async def get_statistics(self) -> Dict:
        """Get overall trading statistics."""
        try:
            cursor = self._connection.cursor()
            
            cursor.execute("SELECT COUNT(*) FROM trades")
            total_trades = cursor.fetchone()[0]
            
            cursor.execute("SELECT SUM(pnl) FROM trades")
            total_pnl = cursor.fetchone()[0] or 0.0
            
            cursor.execute("SELECT COUNT(*) FROM trades WHERE pnl > 0")
            winning_trades = cursor.fetchone()[0]
            
            cursor.execute("SELECT AVG(pnl_percent) FROM trades")
            avg_pnl_percent = cursor.fetchone()[0] or 0.0
            
            cursor.execute("SELECT MAX(max_drawdown) FROM trades")
            max_dd = cursor.fetchone()[0] or 0.0
            
            win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0.0
            
            stats = {
                'total_trades': total_trades,
                'total_pnl': total_pnl,
                'winning_trades': winning_trades,
                'losing_trades': total_trades - winning_trades,
                'win_rate': win_rate,
                'avg_pnl_percent': avg_pnl_percent,
                'max_drawdown': max_dd
            }
            
            self._logger.info(f"Statistics: {total_trades} trades, PnL={total_pnl:.2f}")
            
            return stats
        
        except Exception as e:
            self._logger.error(f"Error retrieving statistics: {e}")
            return {}
    
    def close(self) -> None:
        """Close database connection."""
        if self._connection:
            self._connection.close()
            self._logger.info("Database connection closed")
