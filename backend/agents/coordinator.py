import logging
from backend.agents import market_research, competitor, financial, synthesis, scope_check
from backend import jobs

logger = logging.getLogger("coordinator")


def _run_with_retry(agent_module, question: str, agent_name: str, max_attempts: int = 2) -> str:
    """Run an agent, retrying once on failure. Returns an error note if both attempts fail."""
    last_error = None
    for attempt in range(1, max_attempts + 1):
        try:
            logger.info(f"{agent_name}: attempt {attempt}")
            return agent_module.run(question)
        except Exception as e:
            last_error = e
            logger.warning(f"{agent_name} failed on attempt {attempt}: {e}")

    logger.error(f"{agent_name} failed after {max_attempts} attempts: {last_error}")
    return f"({agent_name} could not complete research after {max_attempts} attempts: {last_error})"


def run_research(question: str, job_id: str) -> None:
    """Orchestrates all agents, updating the job tracker as each stage completes."""

    jobs.update_stage(job_id, "scope_check")
    try:
        in_scope = scope_check.is_business_question(question)
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
    market_result = _run_with_retry(market_research, question, "Market Research Agent")
    jobs.set_section(job_id, "market_research", market_result)

    jobs.update_stage(job_id, "competitor_analysis")
    competitor_result = _run_with_retry(competitor, question, "Competitor Agent")
    jobs.set_section(job_id, "competitor_analysis", competitor_result)

    jobs.update_stage(job_id, "financial_analysis")
    financial_result = _run_with_retry(financial, question, "Financial Agent")
    jobs.set_section(job_id, "financial_analysis", financial_result)

    jobs.update_stage(job_id, "synthesis")
    try:
        final_report = synthesis.run(
            question=question,
            market=market_result,
            competitor=competitor_result,
            financial=financial_result,
        )
    except Exception as e:
        logger.error(f"Synthesis Agent failed: {e}")
        final_report = (
            "Synthesis failed, but here are the raw findings:\n\n"
            f"Market Research:\n{market_result}\n\n"
            f"Competitor Analysis:\n{competitor_result}\n\n"
            f"Financial Analysis:\n{financial_result}"
        )

    jobs.complete_job(job_id, final_report)