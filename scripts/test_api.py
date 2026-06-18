"""Test Flask API endpoints"""
import subprocess, time, requests, json, signal

# Start Flask in background
proc = subprocess.Popen(
    ["python3", "scripts/param_server.py"],
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT,
    cwd="/workspace/quant-trading-system"
)
time.sleep(3)

try:
    # Test /api/status
    r = requests.get('http://localhost:8081/api/status', timeout=5)
    print(f"/api/status: {r.status_code}")
    data = r.json()
    print(f"  loaded: {data.get('loaded')}")
    print(f"  dims: {data.get('dims')}")
    
    # Test /api/strategies
    r2 = requests.get('http://localhost:8081/api/strategies', timeout=5)
    print(f"\n/api/strategies: {r2.status_code}")
    strategies = r2.json()
    print(f"  Strategies: {list(strategies.keys())}")
    
    # Test /api/backtest with a sample stock
    r3 = requests.get('http://localhost:8081/api/stock/000001/backtest?strategy=score_cross', timeout=30)
    print(f"\n/api/stock/000001/backtest: {r3.status_code}")
    if r3.status_code == 200:
        result = r3.json()
        print(f"  Keys: {list(result.keys())}")
        summary = result.get('summary', {})
        print(f"  summary: {summary}")
        print(f"  trades count: {len(result.get('trades', []))}")
    else:
        print(f"  Error: {r3.text[:200]}")
        
except Exception as e:
    print(f"Error: {e}")
finally:
    proc.terminate()
    proc.wait()
    print("\nFlask server stopped")
