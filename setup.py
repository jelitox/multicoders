from pathlib import Path

from setuptools import find_packages, setup


REPO_ROOT = Path(__file__).resolve().parent
PARROT_ROOT = REPO_ROOT / "_refs" / "ai-parrot" / "packages"

setup(
    name="multicoders",
    version="0.1.0",
    packages=find_packages(),
    install_requires=[
        f"ai-parrot @ {(PARROT_ROOT / 'ai-parrot').as_uri()}",
        f"ai-parrot-tools @ {(PARROT_ROOT / 'ai-parrot-tools').as_uri()}",
        "nest-asyncio>=1.6.0",
    ],
    entry_points={
        "console_scripts": [
            "multicoders=multicoders.__main__:main",
        ],
    },
)
