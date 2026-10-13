# Third-party dependencies

B02–B04 uses normal installed dependencies. No source from the crawler, Muse client,
content skills or other third-party repositories was copied or mechanically rewritten.
There are no adopted source commits/original paths to list for this change. Exact
package versions, registry archive URLs and archive hashes are recorded in `uv.lock`;
upstream dependency license files remain included in their distributions.

Runtime dependencies:

| Dependency | Locked version | License | Reason |
|---|---|---|---|
| pydantic | 2.14.1 | MIT | Mature typed validation and JSON Schema generation; avoid hand-built validators. |
| platformdirs | 4.13.0 | MIT | Mature OS user-directory discovery; avoid hardcoded runtime roots. |
| pydantic-core | 2.50.1 | MIT | Pydantic validation engine. |
| annotated-types | 0.8.0 | MIT | Pydantic annotation support. |
| typing-inspection | 0.4.4 | MIT | Pydantic typing support. |
| typing_extensions | 4.16.0 | PSF-2.0 | Pydantic typing compatibility. |

Development dependencies (not runtime requirements): pytest 9.1.1 (MIT), ruff 0.17.0
(MIT), jsonschema 4.26.0 (MIT). These provide the requested test runner, lint/format
checks and independent schema validation. Transitive dev packages: attrs 26.1.0 (MIT),
iniconfig 2.3.1 (MIT), pluggy 1.6.0 (MIT), jsonschema-specifications 2025.9.1 (MIT),
referencing 0.37.0 (MIT), rpds-py 2026.9.1 (MIT), packaging 26.3 (Apache-2.0 OR
BSD-2-Clause), Pygments 2.21.0 (BSD-2-Clause).

Licenses/versions above were read from the installed distributions' primary METADATA,
not inferred from repository names. Hatchling 1.32.4 is the observed isolated build backend
(MIT, verified from its cached distribution METADATA), used by uv for the src-layout wheel; it is not a runtime dependency. No
project-wide license decision or permission to publish is made by this notice.
