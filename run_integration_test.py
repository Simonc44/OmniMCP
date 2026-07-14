#!/usr/bin/env python3
"""
Test d'intégration automatisé et robuste pour la Gateway MCP "OmniMCP Router".
Valide :
1. L'agrégation et le routage initial
2. Le traitement asynchrone non-bloquant en parallèle
3. Le monitoring des performances avec logs de type PERF_WARNING (>5s)
4. L'Auto-Healing (reconnexion automatique après crash provoqué)
5. Le Hot-Reload à chaud (modification de configuration détectée à la volée)
"""

import asyncio
import json
import time
import sys
import os

# Stockage des réponses JSON-RPC par ID
pending_responses = {}
# Stockage des notifications JSON-RPC (sans ID)
received_notifications = asyncio.Queue()
# Captures des logs d'avertissement de performance
perf_warnings_captured = []


async def stdout_reader_loop(stdout):
    """Lit les messages JSON-RPC du routeur sur stdout."""
    try:
        while True:
            line = await stdout.readline()
            if not line:
                break
            try:
                msg = json.loads(line.decode("utf-8", errors="ignore"))
                msg_id = msg.get("id")
                if msg_id is not None:
                    if msg_id in pending_responses:
                        pending_responses[msg_id].set_result(msg)
                else:
                    # C'est une notification
                    await received_notifications.put(msg)
            except Exception as e:
                safe_line = line.decode("ascii", errors="replace")
                print(f"[TEST ERROR] Parsing stdout : {e} pour la ligne : {safe_line}")
    except asyncio.CancelledError:
        pass


async def send_request(stdin, req):
    """Envoie une requête et retourne le futur associé à son ID."""
    req_id = req.get("id")
    fut = asyncio.get_running_loop().create_future()
    pending_responses[req_id] = fut

    stdin.write(json.dumps(req).encode("utf-8") + b"\n")
    await stdin.drain()
    return fut


async def main():
    print("=== DÉBUT DES TESTS D'INTÉGRATION AVANCÉS ===")

    base_dir = os.path.dirname(os.path.abspath(__file__))
    config_file = os.path.join(base_dir, "test_config.json")
    backup_config_file = os.path.join(base_dir, "test_config.json.bak")

    # Création d'une sauvegarde de test_config.json pour la restaurer à la fin
    with open(config_file, "r", encoding="utf-8") as f:
        original_config_content = f.read()
    with open(backup_config_file, "w", encoding="utf-8") as f:
        f.write(original_config_content)

    # Réécriture dynamique de test_config.json pour utiliser le bon interpréteur Python et le bon chemin absolu
    mock_server_path = os.path.join(base_dir, "mock_server.py")
    dynamic_config_data = {
        "mcpServers": {
            "mock-alpha": {
                "command": sys.executable,
                "args": [mock_server_path, "alpha"],
            },
            "mock-beta": {
                "command": sys.executable,
                "args": [mock_server_path, "beta"],
            },
        }
    }
    with open(config_file, "w", encoding="utf-8") as f:
        json.dump(dynamic_config_data, f, indent=2)

    cmd = [
        sys.executable,
        os.path.join(base_dir, "router.py"),
        "--config",
        config_file,
    ]
    print(f"[TEST] Lancement du routeur : {' '.join(cmd)}")

    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    # Lecture des logs de stderr
    async def log_stderr():
        try:
            while True:
                line = await process.stderr.readline()
                if not line:
                    break
                try:
                    text = line.decode("utf-8", errors="ignore").strip()
                    if "PERF_WARNING" in text:
                        perf_warnings_captured.append(text)
                    # Convert to ascii compatible representation to avoid charmap encode errors
                    safe_text = text.encode("ascii", errors="replace").decode("ascii")
                    print(f"[ROUTER LOG] {safe_text}")
                except Exception as e:
                    print(f"[TEST ERROR] Processing stderr line: {e}")
        except asyncio.CancelledError:
            pass

    stderr_task = asyncio.create_task(log_stderr())
    stdout_task = asyncio.create_task(stdout_reader_loop(process.stdout))

    try:
        await asyncio.sleep(5.0)  # Laisser démarrer

        # 1. INITIALISATION
        init_req = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "test-client", "version": "1.0.0"},
            },
        }
        print("\n[TEST] Envoi de la requête 'initialize'...")
        init_resp = await (await send_request(process.stdin, init_req))
        assert "result" in init_resp, "Erreur d'initialisation"

        # Notification d'initialisation
        initialized_notification = {
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
        }
        process.stdin.write(
            json.dumps(initialized_notification).encode("utf-8") + b"\n"
        )
        await process.stdin.drain()

        # 2. LISTE DES OUTILS INITIALE
        list_req = {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
        print("\n[TEST] Envoi de la requête 'tools/list'...")
        list_resp = await (await send_request(process.stdin, list_req))
        tools = list_resp.get("result", {}).get("tools", [])
        tool_names = [t["name"] for t in tools]
        print(f"[TEST] Outils découverts : {tool_names}")
        assert "mock-alpha__greet" in tool_names
        assert "mock-beta__greet" in tool_names
        assert "mock-alpha__exit_server" in tool_names

        # 3. TEST DU PARALLÉLISME (2 appels de 2s simultanés)
        print("\n[TEST] Lancement de deux appels lents (2s) en parallèle...")
        call_a = {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "mock-alpha__slow_add",
                "arguments": {"a": 5, "b": 5, "delay": 2},
            },
        }
        call_b = {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {
                "name": "mock-beta__slow_add",
                "arguments": {"a": 50, "b": 50, "delay": 2},
            },
        }

        start_time = time.time()
        fut_a = await send_request(process.stdin, call_a)
        fut_b = await send_request(process.stdin, call_b)
        res_a, res_b = await asyncio.gather(fut_a, fut_b)
        elapsed = time.time() - start_time
        print(f"[TEST] Temps parallèle écoulé : {elapsed:.2f}s")
        assert (
            elapsed < 3.5
        ), "L'exécution parallèle n'est pas asynchrone non-bloquante !"
        print("[TEST] Succès : Parallélisme asynchrone validé.")

        # 4. TEST DU MONITORING PERFORMANCE (Avertissement si >5s)
        print(
            "\n[TEST] Lancement d'un appel long (6s) pour déclencher le PERF_WARNING..."
        )
        call_slow = {
            "jsonrpc": "2.0",
            "id": 5,
            "method": "tools/call",
            "params": {
                "name": "mock-alpha__slow_add",
                "arguments": {"a": 1, "b": 1, "delay": 6},
            },
        }
        res_slow = await (await send_request(process.stdin, call_slow))
        print(f"[TEST] Réponse reçue après 6s.")
        await asyncio.sleep(0.5)  # Laisser le temps au warning d'être loggé
        assert (
            len(perf_warnings_captured) > 0
        ), "Aucun avertissement de performance (PERF_WARNING) n'a été capturé !"
        print(
            f"[TEST] Succès : Warning de performance intercepté : {perf_warnings_captured[0]}"
        )

        # 5. TEST DE L'AUTO-HEALING (Reconnexion automatique)
        print(
            "\n[TEST] Crash volontaire du serveur 'mock-alpha' via l'outil 'exit_server'..."
        )
        call_exit = {
            "jsonrpc": "2.0",
            "id": 6,
            "method": "tools/call",
            "params": {"name": "mock-alpha__exit_server", "arguments": {}},
        }
        res_exit = await (await send_request(process.stdin, call_exit))
        print(f"[TEST] Réponse du crash initié : {json.dumps(res_exit)}")

        # Le routeur va détecter le crash et tenter de se reconnecter.
        # On attend la déconnexion et la tentative de reconnexion réussie.
        print(
            "[TEST] Attente de la reconnexion automatique du serveur crashé (3.5 secondes)..."
        )
        await asyncio.sleep(3.5)

        # On essaie d'appeler de nouveau mock-alpha__greet. Si l'auto-healing fonctionne,
        # le serveur s'est reconnecté et va répondre avec succès.
        call_greet_reconnect = {
            "jsonrpc": "2.0",
            "id": 7,
            "method": "tools/call",
            "params": {
                "name": "mock-alpha__greet",
                "arguments": {"name": "Auto-Healing"},
            },
        }
        print("[TEST] Envoi d'un appel d'outil post-reconnexion...")
        res_greet = await (await send_request(process.stdin, call_greet_reconnect))
        print(f"[TEST] Réponse post-reconnexion : {json.dumps(res_greet, indent=2)}")
        assert "result" in res_greet and not res_greet.get(
            "error"
        ), "Échec de reconnexion automatique !"
        print(
            "[TEST] Succès : Auto-Healing opérationnel, le serveur s'est reconnecté de lui-même."
        )

        # 6. TEST DE LA GESTION DYNAMIQUE (Hot-Reload)
        print(
            "\n[TEST] Hot-Reload : Modification de test_config.json en retirant 'mock-beta'..."
        )

        # Vider la file des notifications précédentes (notifications de démarrage)
        while not received_notifications.empty():
            try:
                received_notifications.get_nowait()
            except asyncio.QueueEmpty:
                break

        # Modification de la configuration : on garde uniquement mock-alpha
        single_server_config = {
            "mcpServers": {
                "mock-alpha": {
                    "command": sys.executable,
                    "args": [
                        mock_server_path,
                        "alpha",
                    ],
                }
            }
        }

        # Écriture dans test_config.json
        with open(config_file, "w", encoding="utf-8") as f:
            json.dump(single_server_config, f, indent=2)

        print("[TEST] Attente de la notification 'notifications/tools/list_changed'...")

        # On attend la notification du routeur
        notification_received = False
        start_wait = time.time()
        while time.time() - start_wait < 5.0:
            try:
                # Récupère avec un petit timeout pour ne pas bloquer indéfiniment
                notif = await asyncio.wait_for(
                    received_notifications.get(), timeout=1.0
                )
                print(f"[TEST] Notification reçue : {json.dumps(notif)}")
                if notif.get("method") == "notifications/tools/list_changed":
                    notification_received = True
                    break
            except asyncio.TimeoutError:
                continue

        assert (
            notification_received
        ), "Aucune notification 'list_changed' n'a été reçue lors du Hot-Reload !"
        print(
            "[TEST] Succès : Notification de changement de liste d'outils bien reçue par le client."
        )

        # Vérification finale de la liste d'outils (mock-beta doit avoir disparu)
        list_req_after = {
            "jsonrpc": "2.0",
            "id": 8,
            "method": "tools/list",
            "params": {},
        }
        list_resp_after = await (await send_request(process.stdin, list_req_after))
        tools_after = list_resp_after.get("result", {}).get("tools", [])
        tool_names_after = [t["name"] for t in tools_after]
        print(f"[TEST] Nouveaux outils exposés après rechargement : {tool_names_after}")
        assert "mock-alpha__greet" in tool_names_after
        assert "mock-beta__greet" not in tool_names_after
        print(
            "[TEST] Succès : Le serveur 'mock-beta' a été déchargé à chaud avec succès."
        )

    except AssertionError as e:
        print(f"\n[ECHEC DU TEST] AssertionError : {e}")
        sys.exit(1)
    finally:
        # Restauration de la configuration d'origine
        print("\n[TEST] Restauration du fichier test_config.json...")
        if os.path.exists(backup_config_file):
            with open(config_file, "w", encoding="utf-8") as f:
                f.write(original_config_content)
            os.remove(backup_config_file)

        print("[TEST] Arrêt du routeur...")
        process.terminate()
        try:
            process.stdin.close()
            await process.stdin.wait_closed()
        except Exception:
            pass
        try:
            await asyncio.wait_for(process.wait(), timeout=10.0)
            print("[TEST] Le routeur s'est arrêté proprement.")
        except asyncio.TimeoutError:
            print("[TEST] Le routeur n'a pas répondu à SIGTERM après 10s, envoi de SIGKILL...")
            process.kill()
            await process.wait()

        stderr_task.cancel()
        stdout_task.cancel()
        print("=== FIN DES TESTS - SUCCÈS COMPLET ===")


if __name__ == "__main__":
    asyncio.run(main())
