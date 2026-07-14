#!/usr/bin/env python3
"""
MCP Hub Router (Gateway) - Version Industrielle Robuste

Ce script agit comme un serveur MCP unifié (stdio) qui agrège les outils de plusieurs
sous-serveurs MCP déclarés dans un fichier de configuration JSON.

Fonctionnalités avancées intégrées :
1. Auto-Healing : Reconnexion automatique avec backoff exponentiel pour les sous-serveurs déconnectés.
2. Hot-Reload : Détection à chaud des modifications de mcp_router_config.json.
3. Hook System : Mutation, interception et validation (schéma JSON) des requêtes/réponses.
4. Performance Monitoring : Profilage en temps réel et avertissement PERF_WARNING (>5s) sur stderr.
5. Signal Handling : Nettoyage strict des processus orphelins (Windows & Linux).
"""

import argparse
import asyncio
import json
import logging
import os
import re
import signal
import sys
import time
from contextlib import AsyncExitStack

import anyio
import jsonschema
import mcp.types as types
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.server import Server
from mcp.server.session import ServerSession
from mcp.server.stdio import stdio_server

# Configuration du logging vers stderr et mcp_router.log
log_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mcp_router.log")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(log_file, encoding="utf-8"),
        logging.StreamHandler(sys.stderr),
    ],
)
logger = logging.getLogger("mcp_router")


def sanitize_tool_name(server_name: str, tool_name: str) -> str:
    """Assainit le nom d'outil pour respecter le format MCP ^[a-zA-Z0-9_-]{1,64}$."""
    clean_server = re.sub(r"[^a-zA-Z0-9_-]", "_", server_name)
    clean_tool = re.sub(r"[^a-zA-Z0-9_-]", "_", tool_name)
    full_name = f"{clean_server}__{clean_tool}"
    if len(full_name) <= 64:
        return full_name
    srv_part = clean_server[:20]
    tool_part = clean_tool[:42]
    return f"{srv_part}__{tool_part}"


class SubServerManager:
    """Gère le cycle de vie, la reconnexion et les requêtes vers un sous-serveur MCP."""

    def __init__(self, name: str, config: dict, on_tools_changed):
        self.name = name
        self.config = config
        self.on_tools_changed = on_tools_changed
        self.session = None
        self.stack = None
        self.status = "disconnected"  # disconnected, connecting, connected, failed
        self.tools = []
        self.lifecycle_task = None
        self.connect_event = asyncio.Event()
        self.shutdown_event = asyncio.Event()

        # Paramètres de reconnexion
        self.max_attempts = 5
        self.base_delay = 1.0  # en secondes
        self.max_delay = 16.0

    async def start(self):
        """Démarre la boucle de cycle de vie en arrière-plan."""
        self.shutdown_event.clear()
        self.lifecycle_task = asyncio.create_task(self._lifecycle_loop())

    async def stop(self):
        """Arrête proprement le sous-serveur et libère ses ressources."""
        self.shutdown_event.set()
        if self.lifecycle_task:
            self.lifecycle_task.cancel()
            try:
                await self.lifecycle_task
            except asyncio.CancelledError:
                pass
        await self._cleanup()

    async def _cleanup(self):
        """Ferme la session client et le subprocess stdio."""
        self.session = None
        if self.stack:
            logger.info(f"[{self.name}] Fermeture des connexions et processus...")
            try:
                await self.stack.aclose()
            except Exception as e:
                logger.error(f"[{self.name}] Erreur de nettoyage du stack: {e}")
            self.stack = None
        self.status = "disconnected"
        self.tools = []
        self.connect_event.clear()

    async def _lifecycle_loop(self):
        """Boucle de reconnexion automatique avec backoff exponentiel."""
        attempt = 0
        while not self.shutdown_event.is_set():
            self.status = "connecting"
            self.connect_event.clear()
            self.stack = AsyncExitStack()

            try:
                logger.info(
                    f"[{self.name}] Tentative de connexion (tentative {attempt+1}/{self.max_attempts})..."
                )

                # Préparation des paramètres de démarrage du subprocess
                env = {**os.environ}
                if "env" in self.config:
                    env.update(self.config["env"])

                params = StdioServerParameters(
                    command=self.config["command"],
                    args=self.config.get("args", []),
                    env=env,
                )

                # Établissement de la connexion et de la session
                read_stream, write_stream = await self.stack.enter_async_context(
                    stdio_client(params)
                )
                session = await self.stack.enter_async_context(
                    ClientSession(read_stream, write_stream)
                )
                await session.initialize()

                self.session = session
                self.status = "connected"
                attempt = 0  # Réinitialisation des tentatives en cas de succès
                self.connect_event.set()
                logger.info(
                    f"[{self.name}] Sous-serveur connecté et initialisé avec succès."
                )

                # Récupération et notification des outils
                tools_result = await session.list_tools()
                self.tools = tools_result.tools
                await self.on_tools_changed()

                # Attente active. Si le processus meurt, la tâche stdio_client lèvera
                # une exception qui nous fera passer dans le bloc except.
                while not self.shutdown_event.is_set():
                    await asyncio.sleep(1)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(
                    f"[{self.name}] Erreur détectée dans le sous-serveur : {e}",
                    exc_info=True,
                )
                await self._cleanup()

                attempt += 1
                if attempt >= self.max_attempts:
                    logger.error(
                        f"[{self.name}] Nombre maximal de tentatives ({self.max_attempts}) atteint. Arrêt des tentatives."
                    )
                    self.status = "failed"
                    await self.on_tools_changed()
                    break

                # Calcul du backoff exponentiel
                delay = min(self.max_delay, self.base_delay * (2 ** (attempt - 1)))
                logger.info(f"[{self.name}] Reconnexion dans {delay:.2f} secondes...")
                try:
                    await asyncio.sleep(delay)
                except asyncio.CancelledError:
                    break

    async def call_tool(self, tool_name: str, arguments: dict, timeout: float = 5.0):
        """Appelle un outil avec une attente de grâce si le serveur est en reconnexion."""
        if self.status == "connecting":
            logger.info(
                f"[{self.name}] Serveur en reconnexion. Attente de grâce ({timeout}s) pour '{tool_name}'..."
            )
            try:
                await asyncio.wait_for(self.connect_event.wait(), timeout=timeout)
            except asyncio.TimeoutError:
                raise TimeoutError(
                    f"Le sous-serveur '{self.name}' n'a pas pu se reconnecter dans le délai imparti."
                )

        if self.status != "connected" or not self.session:
            raise RuntimeError(
                f"Le sous-serveur '{self.name}' est hors-ligne (Statut: {self.status})."
            )

        return await self.session.call_tool(tool_name, arguments)


class HookSystem:
    """Système de pipeline d'interception et de mutation de requêtes et de réponses."""

    def __init__(self):
        self.request_hooks = []
        self.response_hooks = []

    def register_request_hook(self, func):
        self.request_hooks.append(func)
        return func

    def register_response_hook(self, func):
        self.response_hooks.append(func)
        return func

    async def apply_request_hooks(
        self, server_name: str, tool_name: str, arguments: dict
    ) -> dict:
        current_args = arguments
        for hook in self.request_hooks:
            current_args = await hook(server_name, tool_name, current_args)
        return current_args

    async def apply_response_hooks(
        self, server_name: str, tool_name: str, result: types.CallToolResult
    ) -> types.CallToolResult:
        current_result = result
        for hook in self.response_hooks:
            current_result = await hook(server_name, tool_name, current_result)
        return current_result


class RouterServer(Server):
    """Subclass du Server de base MCP pour capturer la session client stdio active."""

    def __init__(self, name: str, **kwargs):
        super().__init__(name, **kwargs)
        self.active_sessions = set()

    async def run(
        self,
        read_stream,
        write_stream,
        initialization_options,
        raise_exceptions=False,
        stateless=False,
    ):
        """Override de run pour capturer la session et envoyer des notifications."""
        async with AsyncExitStack() as stack:
            lifespan_context = await stack.enter_async_context(self.lifespan(self))
            session = await stack.enter_async_context(
                ServerSession(
                    read_stream,
                    write_stream,
                    initialization_options,
                    stateless=stateless,
                )
            )

            # Enregistrement de la session active
            self.active_sessions.add(session)
            stack.callback(self.active_sessions.discard, session)

            task_support = (
                self._experimental_handlers.task_support
                if self._experimental_handlers
                else None
            )
            if task_support is not None:
                task_support.configure_session(session)
                await stack.enter_async_context(task_support.run())

            async with anyio.create_task_group() as tg:
                async for message in session.incoming_messages:
                    tg.start_soon(
                        self._handle_message,
                        message,
                        session,
                        lifespan_context,
                        raise_exceptions,
                    )

    async def notify_tool_list_changed(self):
        """Envoie la notification standard tools/list_changed à tous les clients connectés."""
        for session in list(self.active_sessions):
            try:
                logger.info(
                    "Notification du client principal : changement dans la liste des outils."
                )
                await session.send_tool_list_changed()
            except Exception as e:
                logger.error(
                    f"Erreur lors de l'envoi de la notification de modification d'outils : {e}"
                )


class RouterGateway:
    """Orchestrateur principal du routeur MCP (Hot-reload, Routing, Profiling)."""

    def __init__(self, config_path: str):
        self.config_path = config_path
        self.servers = {}  # name -> SubServerManager
        self.tool_to_server = {}  # exposed_name -> (manager, original_name, orig_tool)
        self.hook_system = HookSystem()
        self.router_server = RouterServer("mcp-router")
        self.last_config_mtime = 0
        self.update_lock = asyncio.Lock()
        self.watch_task = None

        # Enregistrement des hooks par défaut
        self._register_default_hooks()

        # Décoration des endpoints du serveur routeur principal
        self._setup_handlers()

    def _register_default_hooks(self):
        """Enregistre les hooks par défaut pour l'assainissement et la sécurité."""

        @self.hook_system.register_response_hook
        async def truncate_large_responses(
            server_name: str, tool_name: str, result: types.CallToolResult
        ) -> types.CallToolResult:
            """Tronque les réponses de texte trop longues (> 50k caractères) pour économiser les tokens."""
            max_chars = 50000
            modified_content = []
            for block in result.content:
                if isinstance(block, types.TextContent):
                    if len(block.text) > max_chars:
                        diff = len(block.text) - max_chars
                        logger.warning(
                            f"[HOOK] Le contenu de l'outil '{server_name}__{tool_name}' ({len(block.text)} caractères) "
                            f"dépasse la limite de {max_chars}. Tronquage..."
                        )
                        truncated_text = (
                            block.text[:max_chars]
                            + f"\n\n[... ROUTEUR MCP : CONTENU TRONQUÉ DE {diff} CARACTÈRES POUR ÉVITER L'EXPLOSION DU CONTEXTE ...]"
                        )
                        modified_content.append(
                            types.TextContent(type="text", text=truncated_text)
                        )
                        continue
                modified_content.append(block)
            result.content = modified_content
            return result

    def _setup_handlers(self):
        """Définit les méthodes d'exposition d'outils et de routage."""

        @self.router_server.list_tools()
        async def handle_list_tools():
            """Retourne la liste combinée de tous les outils des sous-serveurs."""
            async with self.update_lock:
                exposed_tools = []
                for exposed_name, (
                    manager,
                    original_name,
                    orig_tool,
                ) in self.tool_to_server.items():
                    # Ne pas exposer d'outils provenant de serveurs HS
                    if manager.status not in ("connected", "connecting"):
                        continue

                    description = orig_tool.description or ""
                    prefix_desc = f"[{manager.name}] {description}".strip()

                    exposed_tools.append(
                        types.Tool(
                            name=exposed_name,
                            description=prefix_desc,
                            inputSchema=orig_tool.inputSchema,
                        )
                    )
                return exposed_tools

        @self.router_server.call_tool()
        async def handle_call_tool(name: str, arguments: dict):
            """Valide, intercepte, exécute avec profiling et renvoie le résultat de l'outil."""
            # Récupération de l'outil dans la table de routage
            tool_entry = self.tool_to_server.get(name)
            if not tool_entry:
                err_msg = f"Outil '{name}' introuvable dans la table de routage."
                logger.error(err_msg)
                return types.CallToolResult(
                    isError=True, content=[types.TextContent(type="text", text=err_msg)]
                )

            manager, original_name, orig_tool = tool_entry

            # 1. Validation stricte du schéma d'entrée
            try:
                jsonschema.validate(instance=arguments, schema=orig_tool.inputSchema)
            except jsonschema.ValidationError as e:
                err_msg = (
                    f"Erreur de validation des arguments pour '{name}' : {e.message}"
                )
                logger.error(err_msg)
                return types.CallToolResult(
                    isError=True, content=[types.TextContent(type="text", text=err_msg)]
                )

            # 2. Application des Hooks de Requête (Mutation)
            try:
                mutated_args = await self.hook_system.apply_request_hooks(
                    manager.name, original_name, arguments
                )
            except Exception as e:
                err_msg = f"Erreur lors de l'exécution du hook de requête : {e}"
                logger.error(err_msg, exc_info=True)
                return types.CallToolResult(
                    isError=True, content=[types.TextContent(type="text", text=err_msg)]
                )

            # 3. Exécution avec Profiling des Performances
            logger.info(
                f"Routage de '{name}' -> sous-serveur '{manager.name}' (outil : '{original_name}')"
            )
            start_time = time.perf_counter()

            try:
                result = await manager.call_tool(original_name, mutated_args)
            except Exception as e:
                err_msg = (
                    f"Erreur lors de l'exécution de l'outil sur '{manager.name}' : {e}"
                )
                logger.error(err_msg, exc_info=True)
                return types.CallToolResult(
                    isError=True, content=[types.TextContent(type="text", text=err_msg)]
                )

            elapsed_ms = (time.perf_counter() - start_time) * 1000.0
            logger.info(
                f"[{manager.name}] '{original_name}' a répondu en {elapsed_ms:.2f}ms"
            )

            if elapsed_ms > 5000.0:
                logger.warning(
                    f"[PERF_WARNING] L'outil '{name}' ({manager.name}:{original_name}) "
                    f"a mis {elapsed_ms/1000.0:.2f} secondes à répondre."
                )

            # 4. Application des Hooks de Réponse (Mutation)
            try:
                mutated_result = await self.hook_system.apply_response_hooks(
                    manager.name, original_name, result
                )
            except Exception as e:
                err_msg = f"Erreur lors de l'exécution du hook de réponse : {e}"
                logger.error(err_msg, exc_info=True)
                return types.CallToolResult(
                    isError=True, content=[types.TextContent(type="text", text=err_msg)]
                )

            return mutated_result

    async def reload_config(self):
        """Met à jour dynamiquement la configuration des sous-serveurs."""
        async with self.update_lock:
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    config = json.load(f)
            except Exception as e:
                logger.error(
                    f"Impossible de décoder le JSON lors du rechargement : {e}"
                )
                return

            new_configs = config.get("mcpServers", {})
            current_names = set(self.servers.keys())
            new_names = set(new_configs.keys())

            to_delete = current_names - new_names
            to_add = new_names - current_names
            to_modify = set()

            for name in current_names & new_names:
                if self.servers[name].config != new_configs[name]:
                    to_modify.add(name)

            # 1. Fermeture des serveurs supprimés
            for name in to_delete:
                logger.info(f"Hot-Reload: Suppression du serveur '{name}'...")
                await self.servers[name].stop()
                del self.servers[name]

            # 2. Redémarrage des serveurs modifiés
            for name in to_modify:
                logger.info(f"Hot-Reload: Modification du serveur '{name}'...")
                await self.servers[name].stop()
                self.servers[name] = SubServerManager(
                    name=name,
                    config=new_configs[name],
                    on_tools_changed=self.on_subserver_tools_changed,
                )
                await self.servers[name].start()

            # 3. Démarrage des nouveaux serveurs
            for name in to_add:
                logger.info(f"Hot-Reload: Ajout du serveur '{name}'...")
                self.servers[name] = SubServerManager(
                    name=name,
                    config=new_configs[name],
                    on_tools_changed=self.on_subserver_tools_changed,
                )
                await self.servers[name].start()

            if to_delete or to_modify or to_add:
                await self.rebuild_routing_table()
                await self.router_server.notify_tool_list_changed()

    async def on_subserver_tools_changed(self):
        """Callback invoqué par un manager de sous-serveur quand sa liste d'outils évolue."""
        async with self.update_lock:
            await self.rebuild_routing_table()
            await self.router_server.notify_tool_list_changed()

    async def rebuild_routing_table(self):
        """Reconstruit la table de routage globale exposed_name -> (manager, original_name, orig_tool)."""
        new_table = {}
        for server_name, manager in self.servers.items():
            for tool in manager.tools:
                exposed_name = sanitize_tool_name(server_name, tool.name)
                if exposed_name in new_table:
                    collision_srv = new_table[exposed_name][0].name
                    logger.error(
                        f"COLLISION DE NOM : L'outil '{tool.name}' du serveur '{server_name}' "
                        f"conflit avec le nom exposé '{exposed_name}' du serveur '{collision_srv}'. Ignoré."
                    )
                    continue
                new_table[exposed_name] = (manager, tool.name, tool)
        self.tool_to_server = new_table
        logger.info(
            f"Table de routage reconstruite : {len(self.tool_to_server)} outil(s) disponible(s)."
        )

    async def watch_config_loop(self):
        """Surveille le fichier de configuration toutes les 2 secondes."""
        self.last_config_mtime = os.path.getmtime(self.config_path)
        while True:
            try:
                await asyncio.sleep(2)
                mtime = os.path.getmtime(self.config_path)
                if mtime != self.last_config_mtime:
                    logger.info(
                        "Changement détecté dans le fichier de configuration. Rechargement à chaud..."
                    )
                    await self.reload_config()
                    self.last_config_mtime = mtime
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(
                    f"Erreur dans la boucle de surveillance du fichier de configuration : {e}"
                )

    async def start(self):
        """Initialise les sous-serveurs, démarre la surveillance de config et lance le serveur stdio."""
        logger.info("Démarrage du routeur MCP Gateway...")

        # Premier chargement initial
        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                config = json.load(f)
        except Exception as e:
            logger.critical(f"Impossible de charger la configuration initiale : {e}")
            sys.exit(1)

        mcp_servers_config = config.get("mcpServers", {})

        # Lancement de tous les sous-serveurs
        for server_name, server_cfg in mcp_servers_config.items():
            self.servers[server_name] = SubServerManager(
                name=server_name,
                config=server_cfg,
                on_tools_changed=self.on_subserver_tools_changed,
            )
            await self.servers[server_name].start()

        # Lancement du Watcher de configuration en arrière-plan
        self.watch_task = asyncio.create_task(self.watch_config_loop())

        # Démarrage du transport stdio
        logger.info("Démarrage du serveur routeur MCP unifié sur stdio...")
        async with stdio_server() as (server_read, server_write):
            await self.router_server.run(
                server_read,
                server_write,
                self.router_server.create_initialization_options(),
            )

    async def shutdown(self):
        """Coupe tous les processus et serveurs proprement."""
        logger.info("Fermeture générale de la passerelle...")
        if self.watch_task:
            self.watch_task.cancel()
            try:
                await self.watch_task
            except asyncio.CancelledError:
                pass

        # Arrêt parallèle de tous les sous-serveurs
        stop_tasks = [manager.stop() for manager in self.servers.values()]
        if stop_tasks:
            await asyncio.gather(*stop_tasks, return_exceptions=True)

        self.servers.clear()
        self.tool_to_server.clear()
        logger.info(
            "Tous les sous-serveurs ont été fermés proprement. Arrêt du routeur."
        )


async def run_gateway(config_path: str):
    gateway = RouterGateway(config_path)

    # Configuration de la fermeture propre sur signaux (SIGINT, SIGTERM)
    loop = asyncio.get_running_loop()

    # Callback pour arrêter la boucle sur signal
    shutdown_triggered = asyncio.Event()

    def handle_signal():
        logger.info("Signal de fermeture reçu.")
        shutdown_triggered.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            # add_signal_handler n'est pas supporté sous Windows par défaut,
            # donc on attrape l'erreur et on utilise signal.signal à la place.
            loop.add_signal_handler(sig, handle_signal)
        except NotImplementedError:
            # Fallback Windows
            signal.signal(sig, lambda s, f: handle_signal())

    # Lancement du serveur et attente du signal d'arrêt
    gateway_task = asyncio.create_task(gateway.start())

    # On attend soit un signal d'arrêt, soit que la tâche du gateway s'arrête d'elle-même (erreur)
    done, pending = await asyncio.wait(
        [gateway_task, asyncio.create_task(shutdown_triggered.wait())],
        return_when=asyncio.FIRST_COMPLETED,
    )

    # Annulation des tâches restantes
    for t in pending:
        t.cancel()

    await gateway.shutdown()


def main():
    parser = argparse.ArgumentParser(
        description="MCP Hub Router (Gateway) Industrielle"
    )
    parser.add_argument(
        "--config",
        type=str,
        default=os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "mcp_router_config.json"
        ),
        help="Chemin vers le fichier de configuration JSON",
    )
    args = parser.parse_args()

    try:
        anyio.run(run_gateway, args.config)
    except KeyboardInterrupt:
        logger.info("Arrêt demandé par l'utilisateur.")
    except Exception as e:
        logger.critical(f"Erreur critique lors de l'exécution : {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
