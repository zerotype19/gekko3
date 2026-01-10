-- Gekko3 Ledger Schema
-- D1 Database: Audit-focused, timestamped records

-- System Status: Singleton row tracking operational state
CREATE TABLE IF NOT EXISTS system_status (
  id TEXT PRIMARY KEY DEFAULT 'singleton',
  status TEXT NOT NULL CHECK (status IN ('NORMAL', 'LOCKED')) DEFAULT 'NORMAL',
  updated_at INTEGER NOT NULL DEFAULT (unixepoch('now'))
);

-- Initialize system_status if empty
INSERT OR IGNORE INTO system_status (id, status, updated_at) 
VALUES ('singleton', 'NORMAL', unixepoch('now'));

-- Accounts: Equity and buying power snapshots (timestamped)
CREATE TABLE IF NOT EXISTS accounts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  timestamp INTEGER NOT NULL DEFAULT (unixepoch('now')),
  equity REAL NOT NULL,
  buying_power REAL NOT NULL,
  day_pnl REAL NOT NULL DEFAULT 0.0
);

CREATE INDEX idx_accounts_timestamp ON accounts(timestamp DESC);

-- Proposals: Audit log of ALL incoming trade proposals (approved or rejected)
CREATE TABLE IF NOT EXISTS proposals (
  id TEXT PRIMARY KEY,
  timestamp INTEGER NOT NULL DEFAULT (unixepoch('now')),
  symbol TEXT NOT NULL,
  strategy TEXT NOT NULL,
  side TEXT NOT NULL CHECK (side IN ('BUY', 'SELL', 'OPEN', 'CLOSE')),
  quantity INTEGER NOT NULL,
  context_json TEXT NOT NULL, -- Full JSON context from Python Brain
  status TEXT NOT NULL CHECK (status IN ('APPROVED', 'REJECTED')),
  rejection_reason TEXT,
  created_at INTEGER NOT NULL DEFAULT (unixepoch('now'))
);

CREATE INDEX idx_proposals_timestamp ON proposals(timestamp DESC);
CREATE INDEX idx_proposals_status ON proposals(status);
CREATE INDEX idx_proposals_symbol ON proposals(symbol);

-- Orders: Execution tracking (linked to proposals)
CREATE TABLE IF NOT EXISTS orders (
  id TEXT PRIMARY KEY, -- Tradier order ID
  proposal_id TEXT NOT NULL,
  symbol TEXT NOT NULL,
  status TEXT NOT NULL, -- 'pending', 'filled', 'rejected', 'cancelled'
  filled_price REAL,
  quantity INTEGER NOT NULL,
  created_at INTEGER NOT NULL DEFAULT (unixepoch('now')),
  updated_at INTEGER NOT NULL DEFAULT (unixepoch('now')),
  FOREIGN KEY (proposal_id) REFERENCES proposals(id)
);

CREATE INDEX idx_orders_proposal_id ON orders(proposal_id);
CREATE INDEX idx_orders_status ON orders(status);
CREATE INDEX idx_orders_symbol ON orders(symbol);
CREATE INDEX idx_orders_created_at ON orders(created_at DESC);

-- Positions: Current holdings reconciliation
CREATE TABLE IF NOT EXISTS positions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  symbol TEXT NOT NULL,
  quantity INTEGER NOT NULL,
  cost_basis REAL NOT NULL,
  date_acquired INTEGER NOT NULL DEFAULT (unixepoch('now')),
  updated_at INTEGER NOT NULL DEFAULT (unixepoch('now')),
  UNIQUE(symbol, date_acquired) -- Prevent duplicate entries
);

CREATE INDEX idx_positions_symbol ON positions(symbol);
CREATE INDEX idx_positions_date_acquired ON positions(date_acquired DESC);

