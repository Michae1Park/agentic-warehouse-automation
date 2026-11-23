from fastapi import APIRouter

from ..db import fetch_one

router = APIRouter(prefix="/kpis", tags=["kpis"])


@router.get("")
async def kpis():
    return await fetch_one(
        """
        SELECT
            (SELECT COUNT(*) FROM orders WHERE status <> 'pending')::INT AS orders_requested,
            (SELECT COUNT(*) FROM orders WHERE status = 'completed')::INT AS orders_completed,
            (SELECT COUNT(*) FROM exceptions WHERE status <> 'resolved')::INT AS human_interventions,
            (SELECT AVG(EXTRACT(EPOCH FROM finished_at - started_at)) FROM tasks WHERE status = 'succeeded')
                AS avg_execution_s,
            (SELECT COUNT(*) FROM task_faults)::INT AS faults,
            (SELECT COUNT(*) FROM task_faults WHERE recovered)::INT AS faults_recovered,
            (SELECT AVG(llm_ms) FROM agent_runs WHERE finished_at IS NOT NULL) AS avg_agent_llm_ms,
            (SELECT AVG(EXTRACT(EPOCH FROM finished_at - started_at) * 1000)
               FROM agent_runs WHERE finished_at IS NOT NULL) AS avg_agent_turn_ms
        """
    )
