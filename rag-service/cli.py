from __future__ import annotations

import asyncio
from pathlib import Path

import typer

from src.auth.passwords import PasswordHasher
from src.db.repositories import UserRepository
from src.db.session import create_engine, create_session_factory
from src.shared.config import Settings
from src.shared.errors import AppError


app = typer.Typer(help="本机 RAG 知识库管理工具")
admin_app = typer.Typer(help="账户管理员初始化")
app.add_typer(admin_app, name="admin")


async def _create_admin(username: str, password: str, settings: Settings) -> None:
    engine = create_engine(
        settings.database.url.get_secret_value(),
        pool_size=settings.database.pool_size,
        pool_recycle=settings.database.pool_recycle_seconds,
    )
    try:
        factory = create_session_factory(engine)
        async with factory() as session:
            repository = UserRepository(session)
            await repository.create_first_admin(username, PasswordHasher().hash(password))
    finally:
        await engine.dispose()


@admin_app.command("create")
def create_admin(
    username: str,
    config: Path = typer.Option(Path("config.yaml"), exists=True, dir_okay=False),
) -> None:
    password = typer.prompt("密码", hide_input=True, confirmation_prompt=True)
    try:
        settings = Settings.load(config)
        asyncio.run(_create_admin(username, password, settings))
    except AppError as error:
        typer.echo(error.message, err=True)
        raise typer.Exit(code=1) from error
    typer.echo(f"账户管理员 {username.strip()} 已创建")


if __name__ == "__main__":
    app()

