"""Thin shim — all data now lives in /opt/shared_modules/skills_engine/racing.py"""
import sys
sys.path.insert(0, '/opt')
from shared_modules.skills_engine.racing import MOTOGP_SKILLS, NASCAR_SKILLS, get_rider_skills

__all__ = ["MOTOGP_SKILLS", "NASCAR_SKILLS", "get_rider_skills"]
