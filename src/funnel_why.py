"""Last funnel block reason — /why command."""

from core.shared_state import STATE


def get_last_block_reason() -> str:
    return STATE.get('system.last_funnel_block', '') or ''


def set_last_block(stage: str, reason: str):
    if reason and stage in ('risk_block', 'knowledge_block'):
        STATE.set('system.last_funnel_block', reason[:300])
        STATE.set('system.last_funnel_block_stage', stage)


def format_why_report() -> str:
    reason = get_last_block_reason()
    stage = STATE.get('system.last_funnel_block_stage', '')
    if not reason:
        return (
            "❓ *No recent block logged*\n\n"
            "Bot may be waiting for zone, session, or score.\n"
            "Send /funnel for today's pipeline."
        )
    stage_label = 'Knowledge' if 'knowledge' in stage else 'Risk / filters'
    return (
        f"❓ *Why no trade?*\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"Last block ({stage_label}):\n"
        f"  {reason}\n\n"
        f"_Send /funnel for full pipeline_"
    )
