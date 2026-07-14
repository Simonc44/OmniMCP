#!/usr/bin/env python3
"""
Serveur MCP de test (Mock) pour valider le fonctionnement du routeur.
"""

from mcp.server import FastMCP
import asyncio
import sys

# Récupère le nom du mock depuis les arguments pour pouvoir lancer plusieurs instances distinctes
server_name = sys.argv[1] if len(sys.argv) > 1 else "mock"
mcp = FastMCP(f"Mock Server - {server_name}")


@mcp.tool()
def greet(name: str) -> str:
    """Dit bonjour à un utilisateur."""
    return f"[{server_name}] Bonjour, {name} !"


@mcp.tool()
async def slow_add(a: int, b: int, delay: int = 2) -> str:
    """Additionne deux nombres lentement pour tester l'asynchronisme non-bloquant."""
    print(
        f"[{server_name}] Début de l'addition de {a} et {b} (délai: {delay}s)...",
        file=sys.stderr,
    )
    await asyncio.sleep(delay)
    print(f"[{server_name}] Fin de l'addition de {a} et {b}.", file=sys.stderr)
    return f"[{server_name}] Résultat : {a} + {b} = {a + b}"


@mcp.tool()
def exit_server() -> str:
    """Tuer volontairement le processus du serveur pour tester l'Auto-Healing."""
    print(f"[{server_name}] Arrêt volontaire demandé !", file=sys.stderr)
    asyncio.get_running_loop().call_later(0.5, lambda: sys.exit(42))
    return f"[{server_name}] Arrêt initié."


if __name__ == "__main__":
    mcp.run(transport="stdio")
