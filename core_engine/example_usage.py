"""Example usage of Theta Data Core Engine.

This demonstrates how to connect to Theta Data Terminal and fetch market data.

Prerequisites:
1. Theta Terminal v3 running locally (java -jar ThetaTerminalv3.jar)
2. Valid Theta Data credentials in theta_terminal/creds.txt
3. Python dependencies installed (pip install -r requirements.txt)
"""
import asyncio
import os
import sys

# Add core_engine to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core_engine.shared.config import CFG
from core_engine.shared.theta_client import AsyncThetaClient, heartbeat
from core_engine.shared.fetchers import ThetaFetchers


async def main():
    print("=== Theta Data Core Engine Demo ===\n")
    
    # 1. Verify terminal is running
    print("1. Checking Theta Terminal connection...")
    try:
        hb = heartbeat(CFG)
        print(f"   ✓ Heartbeat OK: {hb}")
    except Exception as e:
        print(f"   ✗ Heartbeat failed: {e}")
        print("   Make sure ThetaTerminalv3.jar is running on http://127.0.0.1:25510")
        return
    
    # 2. Create client and fetchers
    print("\n2. Creating async client...")
    async with AsyncThetaClient(CFG) as client:
        fetchers = ThetaFetchers(client)
        
        # 3. Get spot price for SPY
        print("\n3. Fetching SPY spot price...")
        spot = await fetchers.spot_price("SPY", is_index=False)
        if spot:
            print(f"   ✓ SPY spot: ${spot:.2f}")
        else:
            print("   ✗ Failed to get SPY spot price")
            return
        
        # 4. Get option expirations
        print("\n4. Fetching SPY option expirations...")
        expirations = await fetchers.list_expirations("SPY")
        if expirations:
            print(f"   ✓ Found {len(expirations)} expirations")
            print(f"   Nearest: {expirations[0]}")
        else:
            print("   ✗ No expirations found")
            return
        
        # 5. Get option chain with Greeks for nearest expiration
        print(f"\n5. Fetching option chain Greeks for {expirations[0]}...")
        chain = await fetchers.option_chain_greeks_snapshot(
            "SPY", 
            expirations[0], 
            annual_div=0.0,  # SPY pays dividends, but using 0 for demo
            spot=spot
        )
        
        if not chain.empty:
            print(f"   ✓ Retrieved {len(chain)} option contracts")
            print(f"   Columns: {list(chain.columns)}")
            print(f"\n   Sample data:")
            print(chain[["strike", "option_type", "bid", "ask", "mid_price", "iv_api", "delta_api", "gamma_api"]].head(10).to_string())
        else:
            print("   ✗ Empty option chain")
        
        # 6. Get risk-free rate
        print("\n6. Fetching risk-free rate...")
        rate = await fetchers.risk_free_rate_cc()
        print(f"   ✓ Risk-free rate (continuously compounded): {rate:.4%}")
        
        # 7. Get VIX
        print("\n7. Fetching VIX level...")
        vix = await fetchers.index_snapshot_price("VIX")
        if vix:
            print(f"   ✓ VIX: {vix:.2f}")
        else:
            print("   ✗ VIX not available")
        
        # 8. Get dividend yield
        print("\n8. Fetching SPY dividend yield...")
        div_yield = await fetchers.dividend_yield("SPY", spot)
        print(f"   ✓ Dividend yield: {div_yield:.4%}")
    
    print("\n=== Demo complete ===")


if __name__ == "__main__":
    asyncio.run(main())