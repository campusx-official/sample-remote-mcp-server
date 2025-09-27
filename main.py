from fastmcp import FastMCP
import random
import json

# Create the FastMCP server instance
mcp = FastMCP("Simple Calculator Server")

# Tool: Add two numbers
@mcp.tool
def add(a: int, b: int) -> int:
    """Add two numbers together.
    
    Args:
        a: First number
        b: Second number
    
    Returns:
        The sum of a and b
    """
    return a + b

# Tool: Generate a random number
@mcp.tool
def random_number(min_val: int = 1, max_val: int = 100) -> int:
    """Generate a random number within a range.
    
    Args:
        min_val: Minimum value (default: 1)
        max_val: Maximum value (default: 100)
    
    Returns:
        A random integer between min_val and max_val
    """
    return random.randint(min_val, max_val)

# Resource: Server information
@mcp.resource("info://server")
def server_info() -> str:
    """Get information about this server."""
    info = {
        "name": "Simple Calculator Server",
        "version": "1.0.0",
        "description": "A basic MCP server with math tools",
        "tools": ["add", "random_number"],
        "author": "Your Name"
    }
    return json.dumps(info, indent=2)

# Prompt: Math problem template
@mcp.prompt
def math_problem(operation: str = "addition") -> list:
    """Generate a math problem prompt.
    
    Args:
        operation: Type of math operation (addition, random)
    
    Returns:
        A message list for the LLM
    """
    if operation == "addition":
        return [
            {
                "role": "user",
                "content": "I need help with an addition problem. Can you use the add tool to calculate some numbers for me?"
            }
        ]
    elif operation == "random":
        return [
            {
                "role": "user", 
                "content": "Generate some random numbers for me using the random_number tool."
            }
        ]
    else:
        return [
            {
                "role": "user",
                "content": f"Help me with a {operation} problem using the available tools."
            }
        ]

# Start the server
if __name__ == "__main__":
    mcp.run(transport="http", port=8000)