import csv
import io
import os
from functools import wraps

from flask import (
    Flask,
    Response,
    render_template,
    request,
)

from sqlalchemy import select

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from database import Participant, Squad


# ============================================================
# SETTINGS
# ============================================================

DATABASE_URL = os.getenv(
    "DATABASE_URL"
)

ADMIN_USER = os.getenv(
    "ADMIN_USER",
    "admin",
)

ADMIN_PASSWORD = os.getenv(
    "ADMIN_PASSWORD"
)


if not DATABASE_URL:

    raise RuntimeError(
        "DATABASE_URL не указан"
    )


if not ADMIN_PASSWORD:

    raise RuntimeError(
        "ADMIN_PASSWORD не указан"
    )


# ============================================================
# DATABASE
# ============================================================

engine = create_async_engine(
    DATABASE_URL,
    pool_pre_ping=True,
)

SessionLocal = async_sessionmaker(
    engine,
    expire_on_commit=False,
    class_=AsyncSession,
)


# ============================================================
# FLASK
# ============================================================

app = Flask(
    __name__
)


# ============================================================
# BASIC AUTH
# ============================================================

def admin_required(function):

    @wraps(function)
    def wrapper(
        *args,
        **kwargs,
    ):

        auth = request.authorization

        if not auth:

            return Response(
                "Authentication required",
                401,
                {
                    "WWW-Authenticate":
                    'Basic realm="Admin"',
                },
            )

        if auth.username != ADMIN_USER:

            return Response(
                "Invalid username",
                401,
                {
                    "WWW-Authenticate":
                    'Basic realm="Admin"',
                },
            )

        if auth.password != ADMIN_PASSWORD:

            return Response(
                "Invalid password",
                401,
                {
                    "WWW-Authenticate":
                    'Basic realm="Admin"',
                },
            )

        return function(
            *args,
            **kwargs,
        )

    return wrapper


# ============================================================
# DASHBOARD
# ============================================================

@app.get("/")
@admin_required
async def dashboard():

    async with SessionLocal() as session:

        squads = list(
            (
                await session.scalars(
                    select(Squad)
                    .order_by(Squad.id)
                )
            ).all()
        )

        participants = list(
            (
                await session.scalars(
                    select(Participant)
                    .order_by(
                        Participant.squad_id,
                        Participant.id,
                    )
                )
            ).all()
        )

    by_squad = {}

    for squad in squads:

        by_squad[squad.id] = []

    for participant in participants:

        by_squad.setdefault(
            participant.squad_id,
            [],
        ).append(
            participant
        )

    return await render_template(
        "index.html",
        squads=squads,
        by_squad=by_squad,
        total=len(participants),
    )


# ============================================================
# CSV
# ============================================================

@app.get("/export.csv")
@admin_required
async def export_csv():

    async with SessionLocal() as session:

        squads = list(
            (
                await session.scalars(
                    select(Squad)
                    .order_by(Squad.id)
                )
            ).all()
        )

        participants = list(
            (
                await session.scalars(
                    select(Participant)
                    .order_by(
                        Participant.squad_id,
                        Participant.id,
                    )
                )
            ).all()
        )

    squad_names = {
        squad.id: squad.name
        for squad in squads
    }

    output = io.StringIO()

    writer = csv.writer(
        output
    )

    writer.writerow(
        [
            "Отряд",
            "Имя",
            "Фамилия",
            "Никнейм",
            "Telegram ID",
        ]
    )

    for participant in participants:

        username = ""

        if participant.username:

            username = (
                "@"
                + participant.username
            )

        writer.writerow(
            [
                squad_names.get(
                    participant.squad_id,
                    "",
                ),
                participant.first_name or "",
                participant.last_name or "",
                username,
                participant.telegram_id,
            ]
        )

    return Response(
        "\ufeff"
        + output.getvalue(),
        mimetype="text/csv; charset=utf-8",
        headers={
            "Content-Disposition":
            "attachment; "
            "filename=participants.csv",
        },
    )


# ============================================================
# HEALTH
# ============================================================

@app.get("/health")
async def health():

    return {
        "status": "ok"
    }


# ============================================================
# LOCAL START
# ============================================================

if __name__ == "__main__":

    port = int(
        os.getenv(
            "PORT",
            "8080",
        )
    )

    app.run(
        host="0.0.0.0",
        port=port,
    )
