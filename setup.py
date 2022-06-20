#################### Maintained by Hatch ####################
# This file is auto-generated by hatch. If you'd like to customize this file
# please add your changes near the bottom marked for 'USER OVERRIDES'.
# EVERYTHING ELSE WILL BE OVERWRITTEN by hatch.
#############################################################
from io import open

from setuptools import find_packages, setup

with open("fluctus/__init__.py", "r") as f:
    for line in f:
        if line.startswith("__version__"):
            version = line.strip().split("=")[1].strip(" '\"")
            break
    else:
        version = "0.0.1"

with open("README.rst", "r", encoding="utf-8") as f:
    readme = f.read()

REQUIRES = ["numpy", "scipy", "matplotlib", "scikit-learn", "nilearn", "nibabel"]

kwargs = {
    "name": "fluctus",
    "version": version,
    "description": "",
    "long_description": readme,
    "author": "Daniel Gomez",
    "author_email": "d.gomez@posteo.org",
    "maintainer": "Daniel Gomez",
    "maintainer_email": "d.gomez@posteo.org",
    "url": "https://github.com/dangom/fluctus",
    "license": "MIT/Apache-2.0",
    "classifiers": [
        "Development Status :: 4 - Beta",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: MIT License",
        "License :: OSI Approved :: Apache Software License",
        "Natural Language :: English",
        "Operating System :: OS Independent",
        "Programming Language :: Python :: 3.7",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: Implementation :: CPython",
        "Programming Language :: Python :: Implementation :: PyPy",
    ],
    "install_requires": REQUIRES,
    "tests_require": ["coverage", "pytest"],
    "packages": find_packages(exclude=("tests", "tests.*")),
}


setup(**kwargs)
