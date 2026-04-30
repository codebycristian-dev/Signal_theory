"""Packaging definition for RTL Sweep Pro."""
from setuptools import setup, find_packages

setup(
    name="rtl_sweep_pro",
    version="1.0.0",
    description="Professional progressive spectrum sweep for RTL-SDR.",
    author="RTL Sweep Pro contributors",
    license="MIT",
    packages=find_packages(),
    python_requires=">=3.9",
    install_requires=[
        "numpy>=1.24",
        "scipy>=1.10",
        "PyQt6>=6.5",
        "pyqtgraph>=0.13",
        "h5py>=3.9",
        "matplotlib>=3.7",
    ],
    extras_require={
        "rtlsdr": ["pyrtlsdr>=0.3.0"],
    },
    entry_points={
        "gui_scripts": [
            "rtl-sweep-pro = main:main",
        ],
    },
)
