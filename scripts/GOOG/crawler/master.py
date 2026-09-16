"""
GOOG 长期数据管道主控 - 一键跑全量外部数据补齐
用法: python master.py
产出: data/GOOG/*/*.json + *.md
"""
import subprocess, sys, pathlib
from pathlib import Path

ROOT=Path(__file__).parent

def run_script(name):
    print(f"\n=== Running {name} ===")
    try:
        subprocess.run([sys.executable, str(ROOT/name)], check=False, timeout=60)
    except Exception as e:
        print(f"{name} failed {e}")

def main():
    for s in ["options_cboe.py","sec_edgar.py","market_data.py","news_macro.py"]:
        run_script(s)
    print("\n=== All crawlers done ===")
    # List outputs
    data_dir=Path("/home/user/GOOG/data/GOOG")
    for p in data_dir.rglob("*"):
        if p.is_file():
            print(p)

if __name__=="__main__":
    main()
