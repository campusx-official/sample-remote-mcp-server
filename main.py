from fastmcp import FastMCP, Context
from fastmcp.utilities.logging import get_logger
import os
import sqlite3
import tempfile
import json
import aiosqlite
import asyncio
from typing import Optional, Dict, Any, List
from datetime import datetime
import anyio

# Configure logging
logger = get_logger(__name__)

# Use environment variables for configuration
DB_PATH = os.environ.get("DB_PATH", os.path.join(tempfile.gettempdir(), "expenses.db"))
DEFAULT_CATEGORIES_JSON = {
    "categories": [
        "Food & Dining",
        "Transportation", 
        "Shopping",
        "Entertainment",
        "Bills & Utilities",
        "Healthcare",
        "Travel",
        "Education",
        "Business",
        "Other"
    ]
}

mcp = FastMCP("ExpenseTracker")

# Database connection semaphore for connection pooling
_db_semaphore = asyncio.Semaphore(10)  # Max 10 concurrent connections

async def get_db_connection() -> aiosqlite.Connection:
    """Get an async database connection with proper configuration."""
    conn = await aiosqlite.connect(DB_PATH, timeout=30.0)
    await conn.execute("PRAGMA journal_mode=WAL")
    await conn.execute("PRAGMA foreign_keys=ON")
    await conn.execute("PRAGMA synchronous=NORMAL")
    return conn

async def init_db():
    """Initialize database with proper error handling and logging."""
    try:
        logger.info(f"Initializing database at: {DB_PATH}")
        
        async with _db_semaphore:
            async with await get_db_connection() as conn:
                # Create expenses table
                await conn.execute("""
                    CREATE TABLE IF NOT EXISTS expenses(
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        date TEXT NOT NULL CHECK(date != ''),
                        amount REAL NOT NULL CHECK(amount >= 0),
                        category TEXT NOT NULL CHECK(category != ''),
                        subcategory TEXT DEFAULT '',
                        note TEXT DEFAULT '',
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                
                # Create indexes for better performance
                await conn.execute("CREATE INDEX IF NOT EXISTS idx_expenses_date ON expenses(date)")
                await conn.execute("CREATE INDEX IF NOT EXISTS idx_expenses_category ON expenses(category)")
                
                # Test write access
                await conn.execute("BEGIN IMMEDIATE")
                await conn.execute("INSERT OR REPLACE INTO expenses(id, date, amount, category, note) VALUES (-1, '2000-01-01', 0, 'test', 'init test')")
                await conn.execute("DELETE FROM expenses WHERE id = -1")
                await conn.commit()
                
                logger.info("Database initialized successfully with write access")
                
    except Exception as e:
        logger.error(f"Database initialization failed: {e}")
        raise

def validate_date(date_str: str) -> bool:
    """Validate date format (YYYY-MM-DD)."""
    try:
        datetime.strptime(date_str, "%Y-%m-%d")
        return True
    except ValueError:
        return False

def validate_amount(amount: float) -> bool:
    """Validate amount is positive."""
    return isinstance(amount, (int, float)) and amount >= 0

# Use anyio to run the sync init in an async context
async def async_init():
    """Initialize the database asynchronously."""
    await init_db()

@mcp.tool()
async def add_expense(
    date: str, 
    amount: float, 
    category: str, 
    subcategory: str = "", 
    note: str = "",
    ctx: Context = None
) -> Dict[str, Any]:
    """Add a new expense entry to the database.
    
    Args:
        date: Expense date in YYYY-MM-DD format
        amount: Expense amount (must be >= 0)
        category: Expense category (required)
        subcategory: Optional subcategory
        note: Optional note/description
        ctx: MCP context (automatically injected)
        
    Returns:
        Dict with status, id (if successful), and message
    """
    # Input validation
    if not validate_date(date):
        return {"status": "error", "message": "Invalid date format. Use YYYY-MM-DD"}
    
    if not validate_amount(amount):
        return {"status": "error", "message": "Amount must be a positive number"}
        
    if not category or not category.strip():
        return {"status": "error", "message": "Category is required"}
    
    try:
        async with _db_semaphore:
            async with await get_db_connection() as conn:
                cur = await conn.execute(
                    """INSERT INTO expenses(date, amount, category, subcategory, note) 
                       VALUES (?, ?, ?, ?, ?)""",
                    (date, amount, category.strip(), subcategory.strip(), note.strip())
                )
                expense_id = cur.lastrowid
                await conn.commit()
                
                logger.info(f"Added expense: ID={expense_id}, amount={amount}, category={category}")
                if ctx:
                    await ctx.log_info(f"Expense added: ${amount:.2f} for {category}")
                
                return {
                    "status": "success", 
                    "id": expense_id, 
                    "message": f"Expense of ${amount:.2f} added successfully"
                }
                
    except aiosqlite.IntegrityError as e:
        error_msg = f"Data validation error: {str(e)}"
        logger.error(error_msg)
        if ctx:
            await ctx.log_error(error_msg)
        return {"status": "error", "message": error_msg}
    except aiosqlite.OperationalError as e:
        if "readonly" in str(e).lower():
            error_msg = "Database is in read-only mode. Check file permissions."
        else:
            error_msg = f"Database error: {str(e)}"
        logger.error(error_msg)
        if ctx:
            await ctx.log_error(error_msg)
        return {"status": "error", "message": error_msg}
    except Exception as e:
        error_msg = f"Unexpected error: {str(e)}"
        logger.error(error_msg)
        if ctx:
            await ctx.log_error(error_msg)
        return {"status": "error", "message": error_msg}

@mcp.tool()
async def list_expenses(
    start_date: str, 
    end_date: str, 
    ctx: Context = None
) -> List[Dict[str, Any]]:
    """List expense entries within an inclusive date range.
    
    Args:
        start_date: Start date in YYYY-MM-DD format
        end_date: End date in YYYY-MM-DD format
        ctx: MCP context (automatically injected)
        
    Returns:
        List of expense dictionaries or error info
    """
    # Input validation
    if not validate_date(start_date) or not validate_date(end_date):
        return [{"error": "Invalid date format. Use YYYY-MM-DD"}]
    
    try:
        async with _db_semaphore:
            async with await get_db_connection() as conn:
                async with conn.execute(
                    """
                    SELECT id, date, amount, category, subcategory, note, created_at
                    FROM expenses
                    WHERE date BETWEEN ? AND ?
                    ORDER BY date DESC, id DESC
                    """,
                    (start_date, end_date)
                ) as cur:
                    rows = await cur.fetchall()
                    cols = [description[0] for description in cur.description]
                    results = [dict(zip(cols, row)) for row in rows]
                
                logger.info(f"Listed {len(results)} expenses from {start_date} to {end_date}")
                if ctx:
                    await ctx.log_info(f"Retrieved {len(results)} expenses")
                
                return results
                
    except Exception as e:
        error_msg = f"Error listing expenses: {str(e)}"
        logger.error(error_msg)
        if ctx:
            await ctx.log_error(error_msg)
        return [{"error": error_msg}]

@mcp.tool()
async def summarize(
    start_date: str, 
    end_date: str, 
    category: Optional[str] = None,
    ctx: Context = None
) -> List[Dict[str, Any]]:
    """Summarize expenses by category within an inclusive date range.
    
    Args:
        start_date: Start date in YYYY-MM-DD format
        end_date: End date in YYYY-MM-DD format
        category: Optional category filter
        ctx: MCP context (automatically injected)
        
    Returns:
        List of summary dictionaries by category or error info
    """
    # Input validation
    if not validate_date(start_date) or not validate_date(end_date):
        return [{"error": "Invalid date format. Use YYYY-MM-DD"}]
    
    try:
        async with _db_semaphore:
            async with await get_db_connection() as conn:
                query = """
                    SELECT 
                        category, 
                        SUM(amount) AS total_amount, 
                        COUNT(*) as count,
                        AVG(amount) as avg_amount,
                        MIN(amount) as min_amount,
                        MAX(amount) as max_amount
                    FROM expenses
                    WHERE date BETWEEN ? AND ?
                """
                params = [start_date, end_date]

                if category:
                    query += " AND category = ?"
                    params.append(category.strip())

                query += " GROUP BY category ORDER BY total_amount DESC"

                async with conn.execute(query, params) as cur:
                    rows = await cur.fetchall()
                    cols = [description[0] for description in cur.description]
                    results = [dict(zip(cols, row)) for row in rows]
                
                # Round amounts for better display
                for result in results:
                    for key in ['total_amount', 'avg_amount', 'min_amount', 'max_amount']:
                        if key in result and result[key] is not None:
                            result[key] = round(result[key], 2)
                
                logger.info(f"Summarized expenses: {len(results)} categories from {start_date} to {end_date}")
                if ctx:
                    await ctx.log_info(f"Generated summary for {len(results)} categories")
                
                return results
                
    except Exception as e:
        error_msg = f"Error summarizing expenses: {str(e)}"
        logger.error(error_msg)
        if ctx:
            await ctx.log_error(error_msg)
        return [{"error": error_msg}]

@mcp.tool()
async def get_expense_stats(
    start_date: str, 
    end_date: str,
    ctx: Context = None
) -> Dict[str, Any]:
    """Get overall expense statistics for a date range.
    
    Args:
        start_date: Start date in YYYY-MM-DD format
        end_date: End date in YYYY-MM-DD format
        ctx: MCP context (automatically injected)
        
    Returns:
        Dictionary with overall statistics
    """
    if not validate_date(start_date) or not validate_date(end_date):
        return {"error": "Invalid date format. Use YYYY-MM-DD"}
    
    try:
        async with _db_semaphore:
            async with await get_db_connection() as conn:
                async with conn.execute(
                    """
                    SELECT 
                        COUNT(*) as total_expenses,
                        SUM(amount) as total_amount,
                        AVG(amount) as avg_amount,
                        MIN(amount) as min_amount,
                        MAX(amount) as max_amount,
                        COUNT(DISTINCT category) as unique_categories,
                        COUNT(DISTINCT date) as days_with_expenses
                    FROM expenses
                    WHERE date BETWEEN ? AND ?
                    """,
                    (start_date, end_date)
                ) as cur:
                    row = await cur.fetchone()
                    cols = [description[0] for description in cur.description]
                    result = dict(zip(cols, row))
                
                # Round amounts
                for key in ['total_amount', 'avg_amount', 'min_amount', 'max_amount']:
                    if result[key] is not None:
                        result[key] = round(result[key], 2)
                
                logger.info(f"Generated stats for {start_date} to {end_date}")
                if ctx:
                    await ctx.log_info(f"Generated statistics for date range")
                
                return result
                
    except Exception as e:
        error_msg = f"Error getting stats: {str(e)}"
        logger.error(error_msg)
        if ctx:
            await ctx.log_error(error_msg)
        return {"error": error_msg}

@mcp.tool()
async def delete_expense(expense_id: int, ctx: Context = None) -> Dict[str, Any]:
    """Delete an expense by ID.
    
    Args:
        expense_id: The ID of the expense to delete
        ctx: MCP context (automatically injected)
        
    Returns:
        Dict with status and message
    """
    if not isinstance(expense_id, int) or expense_id <= 0:
        return {"status": "error", "message": "Invalid expense ID"}
    
    try:
        async with _db_semaphore:
            async with await get_db_connection() as conn:
                # First check if expense exists
                async with conn.execute("SELECT id, amount, category FROM expenses WHERE id = ?", (expense_id,)) as cur:
                    expense = await cur.fetchone()
                
                if not expense:
                    return {"status": "error", "message": f"Expense with ID {expense_id} not found"}
                
                # Delete the expense
                await conn.execute("DELETE FROM expenses WHERE id = ?", (expense_id,))
                await conn.commit()
                
                logger.info(f"Deleted expense: ID={expense_id}")
                if ctx:
                    await ctx.log_info(f"Deleted expense ID {expense_id}")
                
                return {
                    "status": "success",
                    "message": f"Expense ID {expense_id} deleted successfully"
                }
                
    except Exception as e:
        error_msg = f"Error deleting expense: {str(e)}"
        logger.error(error_msg)
        if ctx:
            await ctx.log_error(error_msg)
        return {"status": "error", "message": error_msg}

@mcp.resource("expense://categories", mime_type="application/json")
async def categories() -> str:
    """Get available expense categories."""
    try:
        # Try to load from file first (if deployed with custom categories)
        categories_path = os.path.join(os.path.dirname(__file__), "categories.json")
        try:
            # Use anyio for async file reading
            content = await anyio.Path(categories_path).read_text(encoding="utf-8")
            # Validate it's proper JSON
            json.loads(content)
            return content
        except FileNotFoundError:
            logger.info("Categories file not found, using defaults")
        except json.JSONDecodeError:
            logger.warning("Categories file contains invalid JSON, using defaults")
        
        # Return default categories
        return json.dumps(DEFAULT_CATEGORIES_JSON, indent=2)
        
    except Exception as e:
        logger.error(f"Error loading categories: {e}")
        return json.dumps({"error": f"Could not load categories: {str(e)}"}, indent=2)

@mcp.resource("expense://health", mime_type="application/json")  
async def health_check() -> str:
    """Health check endpoint for monitoring."""
    try:
        async with _db_semaphore:
            async with await get_db_connection() as conn:
                await conn.execute("SELECT 1")
                return json.dumps({
                    "status": "healthy",
                    "database": "accessible",
                    "timestamp": datetime.now().isoformat()
                })
    except Exception as e:
        return json.dumps({
            "status": "unhealthy", 
            "error": str(e),
            "timestamp": datetime.now().isoformat()
        })

# Configure server for remote deployment
async def main():
    """Main async function to start the server."""
    # Initialize database first
    await async_init()
    
    # Set up proper configuration for remote deployment
    host = os.environ.get("HOST", "0.0.0.0")  # Listen on all interfaces
    port = int(os.environ.get("PORT", 8000))
    log_level = os.environ.get("LOG_LEVEL", "INFO")
    
    logger.info(f"Starting ExpenseTracker MCP server on {host}:{port}")
    logger.info(f"Database location: {DB_PATH}")
    
    await mcp.run_async(
        transport="http",
        host=host,
        port=port,
        log_level=log_level
    )

if __name__ == "__main__":
    asyncio.run(main())