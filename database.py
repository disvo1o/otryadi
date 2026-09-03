from sqlalchemy import BigInteger, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Squad(Base):
    __tablename__ = "squads"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    invite_link: Mapped[str] = mapped_column(String(500), nullable=False)
    members_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    participants: Mapped[list["Participant"]] = relationship(
        back_populates="squad"
    )


class Participant(Base):
    __tablename__ = "participants"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    telegram_id: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        unique=True,
        index=True,
    )

    username: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    first_name: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    last_name: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    squad_id: Mapped[int] = mapped_column(
        ForeignKey("squads.id"),
        nullable=False,
        index=True,
    )

    squad: Mapped[Squad] = relationship(
        back_populates="participants"
    )

    __table_args__ = (
        UniqueConstraint(
            "telegram_id",
            name="uq_participant_telegram_id",
        ),
    )
