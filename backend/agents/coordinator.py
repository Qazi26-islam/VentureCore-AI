import logging
from backend.agents import market_research, competitor, financial, synthesis, scope_check
from backend import jobs
from backend.observability import instrument_client, run_traced_agent

logger = logging.getLogger("coordinator")


def _run_with_retry(
    agent_module, question: str, agent_name: str, depth: str,
    organization_id: int, job_id: str, max_attempts: int = 2,
) -> str:
    last_error = None
    for attempt in range(1, max_attempts + 1):
        try:
            logger.info(f"{agent_name}: attempt {attempt} (depth={depth})")
            agent_module.client = instrument_client(agent_module.client)
            return run_traced_agent(
                agent_name, organization_id, question,
                lambda: agent_module.run(question, depth=depth), job_id,
            )
        except Exception as e:
            last_error = e
            logger.warning(f"{agent_name} failed on attempt {attempt}: {e}")

    logger.error(f"{agent_name} failed after {max_attempts} attempts: {last_error}")
    return f"({agent_name} could not complete research after {max_attempts} attempts: {last_error})"


def run_research(
    question: str, job_id: str, mode: str = "market", depth: str = "standard",
    organization_id: int = 1,
) -> None:
    jobs.update_stage(job_id, "scope_check")
    try:
        scope_check.client = instrument_client(scope_check.client)
        in_scope = run_traced_agent(
            "Scope Check Agent", organization_id, question,
            lambda: scope_check.is_business_question(question), job_id,
        )
    except Exception as e:
        logger.warning(f"Scope check failed, proceeding anyway: {e}")
        in_scope = True

    if not in_scope:
        logger.info("Question rejected as out of scope")
        jobs.complete_job(
            job_id,
            "This assistant is designed for business, market, and startup "
            "research questions (e.g. 'Should I open a coffee shop near "
            "campus?'). Your question doesn't appear to be business-related, "
            "so I haven't run the research agents. Try rephrasing it as a "
            "business question and I'll be happy to help.",
        )
        return

    jobs.update_stage(job_id, "market_research")
    market_result = _run_with_retry(
        market_research, question, "Market Research Agent", depth, organization_id, job_id
    )
    jobs.set_section(job_id, "market_research", market_result)

    jobs.update_stage(job_id, "competitor_analysis")
    competitor_result = _run_with_retry(
        competitor, question, "Competitor Agent", depth, organization_id, job_id
    )
    jobs.set_section(job_id, "competitor_analysis", competitor_result)

    jobs.update_stage(job_id, "financial_analysis")
    financial_result = _run_with_retry(
        financial, question, "Financial Agent", depth, organization_id, job_id
    )
    jobs.set_section(job_id, "financial_analysis", financial_result)

    jobs.update_stage(job_id, "synthesis")
    try:
        synthesis.client = instrument_client(synthesis.client)
        final_report = run_traced_agent(
            "Synthesis Agent", organization_id, question,
            lambda: synthesis.run(
                question=question, market=market_result, competitor=competitor_result,
                financial=financial_result, mode=mode, depth=depth,
            ),
            job_id,
        )
    except Exception as e:
        logger.error(f"Synthesis Agent failed: {e}")
        error_str = str(e)
        if "RESOURCE_EXHAUSTED" in error_str or "429" in error_str:
            note = "⚠️ **Daily AI quota reached** — please try again later.\n\n"
        else:
            note = "Synthesis failed unexpectedly, but here are the raw findings:\n\n"

        final_report = (
            f"{note}"
            f"**Market Research:**\n{market_result}\n\n"
            f"**Competitor Analysis:**\n{competitor_result}\n\n"
            f"**Financial Analysis:**\n{financial_result}"
        )

    jobs.complete_job(job_id, final_report)
