from datetime import datetime, time


def is_market_open(now_et: datetime) -> bool:
    """True only on weekdays during US regular hours (09:30–16:00 ET).

    Holiday handling is out of skeleton scope; weekend + hours only.
    """
    if now_et.weekday() >= 5:  # 5=Sat, 6=Sun
        return False
    return time(9, 30) <= now_et.time() < time(16, 0)
