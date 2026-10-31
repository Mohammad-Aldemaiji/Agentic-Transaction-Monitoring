from typing import Any

from psycopg import Connection, OperationalError
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine, ExceptionContext
from sqlalchemy.exc import DBAPIError, SQLAlchemyError, TimeoutError
from sqlalchemy.orm import Session, sessionmaker

from atm_backend.settings import Settings


def failure_details(error: Exception) -> dict[str, str | bool]:
    """Allowlisted diagnostics only: never SQL, parameters or driver messages."""
    details: dict[str, str | bool] = {
        "category": "storage_error" if isinstance(error, SQLAlchemyError) else "processing_error"
    }
    if isinstance(error, TimeoutError):
        details["category"] = "pool_timeout"
    elif isinstance(error, DBAPIError):
        details["category"] = "database_error"
        details["connection_invalidated"] = error.connection_invalidated
        code = getattr(error.orig, "sqlstate", None)
        if isinstance(code, str) and len(code) == 5 and code.isascii() and code.isalnum():
            details["sqlstate"] = code
    return details


def install_disconnect_recovery(engine: Engine) -> None:
    @event.listens_for(engine, "handle_error")
    def classify_ping_failure(context: ExceptionContext) -> None:
        # Psycopg can mask a failed ping with an autocommit-restoration error.
        # Only reconnect before checkout; never replay an in-flight transaction.
        if not context.is_pre_ping:
            return
        error: BaseException | None = context.original_exception
        for _ in range(5):
            if isinstance(error, OperationalError):
                # SQLAlchemy documents this as mutable; its protocol has empty slots.
                context.is_disconnect = True  # type: ignore[misc]
                return
            if error is None:
                return
            error = error.__cause__ or error.__context__


def database(settings: Settings) -> tuple[Engine, sessionmaker[Session]]:
    engine = create_engine(
        settings.database_url.get_secret_value(),
        pool_pre_ping=True,
        pool_recycle=300,
        pool_size=settings.pool_size,
        max_overflow=0,
        pool_timeout=5,
        hide_parameters=True,
        execution_options={"schema_translate_map": {"atm": settings.database_schema}},
        connect_args={
            "connect_timeout": 5,
            "keepalives": 1,
            "keepalives_idle": 30,
            "keepalives_interval": 10,
            "keepalives_count": 3,
            "tcp_user_timeout": 15000,
        },
    )
    install_disconnect_recovery(engine)

    @event.listens_for(engine, "connect")
    def configure_connection(connection: Connection[Any], record: Any) -> None:
        # Session poolers may ignore startup `options`. Set and commit explicitly.
        # Use the session pooler (5432), never transaction pooling for this adapter.
        with connection.cursor() as cursor:
            cursor.execute("SET statement_timeout = '10s'")
            cursor.execute("SET lock_timeout = '3s'")
            cursor.execute("SET idle_in_transaction_session_timeout = '15s'")
            cursor.execute("SET TIME ZONE 'UTC'")
            cursor.execute(
                {
                    "atm": "SET search_path = atm",
                    "atm_test": "SET search_path = atm_test",
                    "atm_booth": "SET search_path = atm_booth",
                }[settings.database_schema]
            )
        connection.commit()

    return engine, sessionmaker(engine, expire_on_commit=False)
