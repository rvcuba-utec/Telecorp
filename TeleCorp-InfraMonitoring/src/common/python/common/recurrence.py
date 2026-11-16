"""Conteo de episodios por antena (RF-AWS-15, §3.4). Un episodio = un episode_id con evento FALLA/SATURACION."""
from datetime import timedelta

from . import aws, ddb, timeutil


def count_episodes(antenna_id: str, now_iso: str):
    """(episodios en 7 días, episodios en 30 días)."""
    now = timeutil.parse(now_iso)
    since30 = timeutil.fmt(now - timedelta(days=30))
    since7 = timeutil.fmt(now - timedelta(days=7))
    rows = ddb.query(aws.table("ANTENNAEVENTS"), "antenna_id = :a AND sk >= :s",
                     {":a": antenna_id, ":s": since30, ":t": "FALLA", ":u": "SATURACION"},
                     filter_expr="event_type IN (:t, :u)", projection="ts, episode_id")
    e30 = {r["episode_id"] for r in rows if r["ts"] <= now_iso}
    e7 = {r["episode_id"] for r in rows if since7 <= r["ts"] <= now_iso}
    return len(e7), len(e30)
