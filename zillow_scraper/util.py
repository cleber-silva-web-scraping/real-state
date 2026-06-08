#!/usr/bin/env python3
"""Utilitarios puros (sem dependencia de estado)."""
import datetime


def now_iso():
    return datetime.datetime.now().isoformat()


def parse_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
