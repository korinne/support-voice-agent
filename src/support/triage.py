"""Clef sorts each new ticket by team and urgency, away from the call's speaking path."""

from agent.config import TRIAGE_MODEL
from server.observability import log
from services.inference import run_inference

TEAMS = {
    "shipping": "Deliveries, tracking, lost or late packages, address changes",
    "billing": "Charges, refunds, store credit, and anything offered to make up for a bad experience",
    "product": "How a gadget works, warranty claims, returns, and cancellations",
    "safety": "A new report of an injury, fuel leak, smoke, fire, or a recalled product",
}
QUESTIONS = {
    "team": {
        "type": "choice",
        # Matt's tickets often retell earlier problems. A request for something
        # free "for the hassle" of a fuel smell went to safety until this said
        # to route on the request.
        "instructions": (
            "Which Acme support team should handle what this ticket asks for? A problem the "
            "ticket mentions only as background, or one that already has its own ticket, "
            "doesn't decide the team."
        ),
        "criteria": TEAMS,
    },
    "urgency": {
        "type": "score",
        "instructions": "How soon does a person need to act on this ticket?",
        "criteria": [
            "Within two business days",
            "Within one business day",
            "Today",
            "Right away, because someone could get hurt",
        ],
    },
    "wants_person": {
        "type": "noul",
        # Matt writes the ticket, so a note like "safety team to follow up" is his,
        # not the customer's. The question excludes follow-up the team plans.
        "instructions": (
            "Did the customer explicitly ask to talk to a person, such as a callback, a "
            "manager, or a human instead of the assistant? Follow-up that the support team "
            "plans on its own doesn't count."
        ),
    },
}


async def triage(ai, ticket, gateway):
    """Returns the chosen team and its probability, urgency from 0 to 3, and
    the probability that the customer asked for a person."""
    result = await run_inference(
        ai,
        TRIAGE_MODEL,
        {
            "model": TRIAGE_MODEL.rsplit("/", 1)[-1],
            "state": {
                "summary": ticket["summary"],
                "details": ticket["details"],
                "order": ticket.get("order_id"),
            },
            "questions": QUESTIONS,
        },
        {"gateway": gateway},
        log=log.bind(provider="triage", model=TRIAGE_MODEL),
        # Nothing replaces a ticket's triage, so the request never goes stale.
        is_current=lambda: True,
    )
    answers = result["answers"]
    team = answers["team"]["choice"]
    return {
        "team": team,
        "team_confidence": round(answers["team"]["probabilities"][team], 3),
        "urgency": round(answers["urgency"]["score"], 2),
        "wants_person": round(answers["wants_person"]["noul"], 3),
    }
