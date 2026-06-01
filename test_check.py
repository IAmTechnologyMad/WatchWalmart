"""Quick test script to verify the stock checker works."""
import sys
sys.path.insert(0, ".")

from app import check_stock

result = check_stock()
print("\n" + "=" * 50)
print("STOCK CHECK RESULT")
print("=" * 50)
for key, value in result.items():
    print(f"  {key}: {value}")
print("=" * 50)
