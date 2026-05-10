"""
server_manager.py

A centralized launcher that gracefully handles the lifecycle 
of both the Naming Server and the Trivia Host Server.
"""
import subprocess
import sys
import time
import os

def run_servers():
    print("=== Distributed Trivia Manager ===")
    
    naming_process = None
    trivia_process = None

    try:
        # 1. Launch the Naming Server
        print("[MANAGER] Booting Naming Server...")
        # We start this in the background
        naming_process = subprocess.Popen([sys.executable, "naming_server.py"])
        
        # Give the Naming Server 2 seconds to bind to ports 4000 and 4001
        time.sleep(2) 

        # 2. Launch the Trivia Server
        print("[MANAGER] Booting Trivia Server...")
        # Since the Naming server is already up, trivia_server.py will 
        # instantly find it and skip its own auto-start routine.
        trivia_process = subprocess.Popen([sys.executable, "trivia_server.py"])

        # 3. Wait for the game to finish
        # This completely blocks the manager script until the Trivia Server 
        # naturally exits at the end of the question array.
        trivia_process.wait()
        
        print("\n[MANAGER] Trivia Server has finished the game.")

    except KeyboardInterrupt:
        print("\n[MANAGER] Interrupted by user (Ctrl+C). Initiating shutdown...")

    finally:
        # 4. Graceful Cleanup
        if naming_process and naming_process.poll() is None:
            print("[MANAGER] Shutting down Naming Server...")
            naming_process.terminate() 
            naming_process.wait() # Ensure the OS fully reclaims the socket
            
        print("[MANAGER] All systems offline. Ready for next game.")

if __name__ == "__main__":
    run_servers()