from app.adapters.base import BookingAdapter
from app.adapters.courtreserve import CourtReserveAdapter
from app.models import Platform

ADAPTER_REGISTRY: dict[Platform, type[BookingAdapter]] = {
    Platform.COURTRESERVE: CourtReserveAdapter,
    # Add new platforms here, e.g. Platform.PLAYBYPOINT: PlayByPointAdapter, and implement
    # a class in app/adapters/<platform>.py that subclasses BookingAdapter. Nothing else in
    # the app (scheduler, worker, models, UI) needs to change to support a new platform.
}


def get_adapter_class(platform: Platform) -> type[BookingAdapter]:
    try:
        return ADAPTER_REGISTRY[platform]
    except KeyError as exc:
        raise ValueError(f"No adapter registered for platform '{platform}'") from exc
