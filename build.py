"""Build the macOS menu-bar application and remove generated intermediates."""
from importlib.util import find_spec
from pathlib import Path
import shutil
import subprocess
import sys


ROOT = Path(__file__).resolve().parent
SPEC = ROOT / "K210CodexDisplayHub.spec"
APP_NAME = "K210 Codex Display Hub.app"


def project_python():
    """Prefer the project's virtual environment when it already exists."""
    candidates = [ROOT / ".venv" / "bin" / "python",
                  ROOT / ".venv" / "Scripts" / "python.exe"]
    for candidate in candidates:
        if candidate.exists():
            return candidate.absolute()
    return Path(sys.executable).absolute()


def clean_generated_files():
    shutil.rmtree(ROOT / "build", ignore_errors=True)
    shutil.rmtree(ROOT / "dist" / "K210 Codex Display Hub", ignore_errors=True)
    for cache in ROOT.rglob("__pycache__"):
        shutil.rmtree(cache, ignore_errors=True)
    for bytecode in ROOT.rglob("*.pyc"):
        bytecode.unlink(missing_ok=True)


def main():
    if sys.platform != "darwin":
        raise SystemExit("当前打包配置仅支持 macOS。")

    python = project_python()
    if python != Path(sys.executable).absolute():
        raise SystemExit(subprocess.call([str(python), str(Path(__file__).resolve())]))

    missing = [name for module, name in (("PyInstaller", "PyInstaller"),
                                          ("PIL", "Pillow"))
               if find_spec(module) is None]
    if missing:
        command = f'"{python}" -m pip install -r "{ROOT / "requirements-build.txt"}"'
        raise SystemExit("缺少打包依赖：%s\n请先运行：%s" % (", ".join(missing), command))

    subprocess.run([str(python), str(ROOT / "tools" / "create_icons.py")],
                   cwd=ROOT, check=True)
    subprocess.run([str(python), "-m", "PyInstaller", "--noconfirm", "--clean",
                    str(SPEC)], cwd=ROOT, check=True)

    app = ROOT / "dist" / APP_NAME
    if not app.exists():
        raise SystemExit("打包完成，但未找到应用：" + str(app))
    subprocess.run(["codesign", "--verify", "--deep", "--strict", str(app)],
                   check=True)
    clean_generated_files()
    print("\n打包完成：" + str(app))


if __name__ == "__main__":
    main()
