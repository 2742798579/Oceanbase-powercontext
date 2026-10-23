# Copyright (c) 2026 OceanBase.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at https://www.apache.org/licenses/LICENSE-2.0

"""Server-owned OceanBase/seekDB VECTOR spaces, provisioned during database initialization."""

import re
from hashlib import sha256

from pyobvector import VECTOR
from sqlalchemy import (
    Column,
    ForeignKeyConstraint,
    Index,
    MetaData,
    Table,
    UniqueConstraint,
    inspect,
    select,
    text,
    update,
)

from powercontext.builtin.artifacts.memory.canonical import canonical_json
from powercontext.builtin.artifacts.memory.models import EmbeddingProfile
from powercontext.builtin.persistence.tables import (
    ARTIFACT_VECTOR_SPACES_TABLE,
    ARTIFACT_VECTORS_TABLE,
    SHARED_METADATA,
)


def native_vector_table(space):
    identity = space["space_id"]
    if re.fullmatch(r"[0-9a-f]{32}", identity) is None or space["physical_table"] != f"pc_av_{identity}":
        raise ValueError("invalid registered vector space table identity")  # noqa: TRY003
    profile = EmbeddingProfile.model_validate(space["profile_json"])
    fingerprint = sha256(canonical_json(profile.model_dump(mode="json"))).hexdigest()
    if (
        (profile.dimension, profile.distance, profile.normalization, fingerprint)
        != (space["dimension"], space["distance"], space["normalization"], space["profile_fingerprint"])
        or profile.dimension < 1
        or space["storage_format_version"] != "float32-le-v1"
    ):
        raise ValueError("inconsistent vector space profile or storage format")  # noqa: TRY003
    metadata = MetaData()
    # Foreign key targets resolve in isolated metadata without provisioning them here.
    for name in ("pc_artifacts", "pc_artifact_search_states"):
        target = SHARED_METADATA.tables[name]
        Table(
            name,
            metadata,
            *(Column(column.name, column.type, primary_key=True) for column in target.primary_key.columns),
        )
    return Table(
        space["physical_table"],
        metadata,
        *(
            Column(
                column.name,
                VECTOR(profile.dimension) if column.name == "embedding" else column.type,
                primary_key=column.primary_key,
                nullable=column.nullable,
            )
            for column in ARTIFACT_VECTORS_TABLE.columns
        ),
        UniqueConstraint("build_id", "index_def_id", "artifact_id", "fragment_id", name=f"uq_av_{identity}"),
        ForeignKeyConstraint(
            ("scope_id", "family", "artifact_id", "revision"),
            ("pc_artifacts.scope_id", "pc_artifacts.family", "pc_artifacts.artifact_id", "pc_artifacts.revision"),
        ),
        ForeignKeyConstraint(
            ("scope_id", "family"), ("pc_artifact_search_states.scope_id", "pc_artifact_search_states.family")
        ),
        Index(f"ix_av_{identity}", "scope_id", "family", "build_id", "space_id", "index_def_id", "artifact_id"),
    )


def verify_native_space(connection, space):  # noqa: C901 - Verify one complete physical space contract.
    table = native_vector_table(space)
    inspector = inspect(connection)
    if not inspector.has_table(table.name):
        raise ValueError("vector space physical table is absent")  # noqa: TRY003
    columns = {column["name"]: column for column in inspector.get_columns(table.name)}
    if set(columns) != set(table.c.keys()):
        raise ValueError("vector space physical columns differ from the registered format")  # noqa: TRY003
    vector_type = connection.execute(
        text(
            "SELECT column_type FROM information_schema.columns WHERE table_schema=DATABASE() AND table_name=:name AND column_name='embedding'"
        ),
        {"name": table.name},
    ).scalar_one()
    if str(vector_type).lower().replace(" ", "") != f"vector({space['dimension']})":
        raise ValueError("vector space physical dimension differs from its profile")  # noqa: TRY003
    if inspector.get_pk_constraint(table.name)["constrained_columns"] != ["vector_id"]:
        raise ValueError("vector space primary key is incompatible")  # noqa: TRY003
    unique = {tuple(item["column_names"]) for item in inspector.get_unique_constraints(table.name)}
    if ("build_id", "index_def_id", "artifact_id", "fragment_id") not in unique:
        raise ValueError("vector space fragment identity constraint is absent")  # noqa: TRY003
    foreign = {
        (tuple(fk["constrained_columns"]), fk["referred_table"], tuple(fk["referred_columns"]))
        for fk in inspector.get_foreign_keys(table.name)
    }
    for constraint in table.foreign_key_constraints:
        if (
            tuple(constraint.columns.keys()),
            constraint.referred_table.name,
            tuple(element.column.name for element in constraint.elements),
        ) not in foreign:
            raise ValueError("vector space foreign key differs from the registered format")  # noqa: TRY003
    actual_indexes = {tuple(item["column_names"]) for item in inspector.get_indexes(table.name)}
    if ("scope_id", "family", "build_id", "space_id", "index_def_id", "artifact_id") not in actual_indexes:
        raise ValueError("vector space search isolation index is absent")  # noqa: TRY003
    collation_rows = connection.execute(
        text(
            "SELECT column_name, collation_name FROM information_schema.columns WHERE table_schema=DATABASE() AND table_name=:name"
        ),
        {"name": table.name},
    ).all()
    identities = {
        "vector_id",
        "scope_id",
        "family",
        "artifact_id",
        "schema_version",
        "index_def_id",
        "build_id",
        "fragment_id",
        "input_hash",
        "space_id",
    }
    if any(collation != "utf8mb4_bin" for name, collation in collation_rows if name in identities):
        raise ValueError("vector space identities require binary collation")  # noqa: TRY003
    if any(column["nullable"] for column in columns.values()):
        raise ValueError("vector space columns must be nonnullable")  # noqa: TRY003


def provision_native_spaces(connection):
    """Create registered spaces using the database initialization connection."""
    spaces = (
        connection
        .execute(select(ARTIFACT_VECTOR_SPACES_TABLE).where(ARTIFACT_VECTOR_SPACES_TABLE.c.backend == "mysql"))
        .mappings()
        .all()
    )
    for space in spaces:
        table = native_vector_table(space)
        table.create(connection, checkfirst=True)
        verify_native_space(connection, space)
        connection.execute(
            update(ARTIFACT_VECTOR_SPACES_TABLE)
            .where(ARTIFACT_VECTOR_SPACES_TABLE.c.space_id == space["space_id"])
            .values(provision_state="ready")
        )
