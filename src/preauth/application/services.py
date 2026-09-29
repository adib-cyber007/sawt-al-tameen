from dataclasses import dataclass

from sqlalchemy.orm import Session, sessionmaker

from preauth.application.callback_service import CallbackService
from preauth.application.case_service import CaseService
from preauth.application.desk_service import DeskService
from preauth.application.evaluation_service import EvaluationService
from preauth.application.query_service import CaseQueryService
from preauth.application.review_service import ReviewService
from preauth.application.voice_channel_service import VoiceChannelService
from preauth.application.voice_execution_service import VoiceExecutionService
from preauth.infrastructure.clock import Clock, SystemClock
from preauth.recommendation.engine import DeterministicRecommendationEngine, RecommendationEngine
from preauth.rules.uae_ruleset import build_uae_rules_engine
from preauth.rules.model import RulesEngine


@dataclass(frozen=True)
class ApplicationServices:
    desk: DeskService
    cases: CaseService
    evaluation: EvaluationService
    review: ReviewService
    queries: CaseQueryService
    callbacks: CallbackService
    voice: VoiceChannelService
    executions: VoiceExecutionService


def build_services(
    session_factory: sessionmaker[Session],
    clock: Clock | None = None,
    rules_engine: RulesEngine | None = None,
    recommendation_engine: RecommendationEngine | None = None,
) -> ApplicationServices:
    clock = clock or SystemClock()
    rules_engine = rules_engine or build_uae_rules_engine()
    recommendation_engine = recommendation_engine or DeterministicRecommendationEngine()
    evaluation = EvaluationService(session_factory, clock, rules_engine, recommendation_engine)
    review = ReviewService(session_factory, clock)
    callbacks = CallbackService(session_factory, clock)
    return ApplicationServices(
        desk=DeskService(session_factory, clock, evaluation, review, callbacks),
        cases=CaseService(session_factory, clock),
        evaluation=evaluation,
        review=review,
        queries=CaseQueryService(session_factory, clock, rules_engine),
        callbacks=callbacks,
        voice=VoiceChannelService(session_factory, clock),
        executions=VoiceExecutionService(session_factory, clock),
    )
