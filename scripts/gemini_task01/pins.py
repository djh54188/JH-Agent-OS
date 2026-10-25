"""Hark-派单-22: trusted anchors of the approved task01 package and of the analysed source.

These constants are the anchor; they are NOT read from package.json or from the export
manifest (both of which an altered input could rewrite together with the files they
describe). They were computed once from Git objects of the fixed commit and from the
package files at the commit that introduced this module; changing any of them is a new
package and needs a new dispatch. No signing or versioning platform is implied.
"""

SOURCE_COMMIT = "f6bf47c59688c43d927dede01b59f772520815d8"
SOURCE_PREFIX = "src/"
TARGET_FILE = "src/agent_session_gemini.py"
TARGET_BLOB = "31d3cd7c111dfdd0d33a65ad097f9cbe3d0fac38"
TASK_ID = "gemini-task01-registry-unknown-no-resend"
TASK_SHA256 = "5ed4f438c57918d7924ea00bf02134c529b20bf9556e6b01c6dffa7830e80518"
REVIEW_BASELINE_SHA256 = "f5ddcbad182fa91ebd48625d59743c6c07c396db47ba828b0fdb512abd58b8c7"
# sha256 of the exact bytes of scripts/gemini_task01/package.json (it pins the two
# digests above, the stdin prefix / -p text, core points, focus lines and ranges).
PACKAGE_SHA256 = "92346a5ffd6f06e07939c269b899f57eed242ddfdfe6dbe850c6abdf3abe746c"

# Every file under src/ at SOURCE_COMMIT: path -> (git blob, size, sha256), from
# `git ls-tree -r` + `git cat-file blob`. The full set, not only the target.
SOURCE_FILES = {
    "src/acceptance.py": ("d956533f28e162a0aec78a3b0371978486d03fd2", 9500, "a5a2223cfd9d0fcac5258b611fecc05ad508d0017c317cb77d766bc82b0260d6"),
    "src/adapters.py": ("29428e5c2c300ffcb8b3a107d226522afd8bd5a7", 19798, "f581d8489bf7679c395e012a3fd59d974314f4f010cea5386b288a0f5ed5c24d"),
    "src/agent_session.py": ("b14d6ca15cb6252af43b689528bc71593d9b29c6", 23813, "b92fbbdaa87d9c7a6d15bd61b6b3cbd2c2393db20489cfa812a258c6d12f1b20"),
    "src/agent_session_codex.py": ("587efb1701aac85dbfe05efe61a069822cf14b8b", 48681, "559fc0a957a84e636d2e8f01c268dbf2c30968276c6582cb0b98bf795fe00007"),
    "src/agent_session_gemini.py": ("31d3cd7c111dfdd0d33a65ad097f9cbe3d0fac38", 76649, "dbe293f54a163b1c6400a77e579db07463adc344323e9142215da44af8b609e2"),
    "src/anthropic_adapter.py": ("b1031673c24a8fed806e8bb9e79f07a7cd41502d", 9909, "48a683c4c1b7dcc7f152b23dca5172314ad18b83152a427b732f9068d408a363"),
    "src/app_api.py": ("4002df45424f2a1f0b5183508e959a582339c3fc", 27550, "c05a1f0c911c15c8cc3186e7a28cdf83ddde1e128b703ac57c29dacc6a1a9b2e"),
    "src/artifacts.py": ("95aa222db79d0902c5b22512ff091435aa22f7c7", 3180, "cc569f7634f4e4c88b6fccb73a6afd23fd967b665fa6aaf11cdcb54daeaa09a1"),
    "src/capture_binding.py": ("e43e0a473e96d64f66868c246726bf55507d4152", 29739, "670baf9a6b9a37cf709b78d41edb2a8382a3c14fd5e4c25f313326bfe0bfbaff"),
    "src/cli.py": ("7516dd32d5f5e4e6806c7d31cb345947e1809ae1", 44372, "38be6b3531cc06c1b929958b07c94a367a53d2d0141cad4eaed51b77b8a39e97"),
    "src/code_delivery.py": ("de3fdb4f0cfa0d4fe76e41ec00cd54f29680db99", 23799, "0a2aa52d0fdf2ebf4e2d8af928ed9ce18650f66435311c77bc60b94f7a57a705"),
    "src/codex_managed.py": ("aa383f986e49e0257a3e90770d40129e2630fc98", 12406, "f5f068678cbb21b87d91c4850dff92a74ee9abde1e1096aed20ef5b4a4d96976"),
    "src/control_guards.py": ("b78b8403f4200ae6deddf3f151e7a1f4a42e3558", 6677, "61c1a0bb3a2af263f2163420f864edd9a1c492897c0c858bb58eacdcb99ae1f9"),
    "src/file_tools.py": ("5b388c9cf0eb7ba11f58ab5dcdac42d72f519f30", 27612, "a5dfeff91047b92231f7f34159a8fd7fee7bf147c5d8b37b800a2e41d5e89e4c"),
    "src/gui_http.py": ("f92491ce846947034194b3aa81959aa95951dda4", 8781, "b55111086acac52499aa9d3fcb8583c552875dc3ee590ec2ab34da8872b7726f"),
    "src/host_capture.py": ("a8ec9ede05cb1de158eed2919467d6af39eebb9f", 24973, "8206e6878821b2f87cb1c63f7f9eb924ef0f8f43ef665f1d754502752e3595cf"),
    "src/memory.py": ("560bfc4ec19c2ecd54fe7763df91a5e77dd55c7a", 3945, "4be3042c39ca90ef8ca54f7e3e270c1156ca76ab106bb97a65a88701ccb767a6"),
    "src/mock_components.py": ("9919b999a65ad2fac4de88de7b8e9f5d32c9bcf6", 19302, "8ca13f2bde51ad84a1de073c7a4a4a80bed04d2170ae5166835d0efd25c3e05e"),
    "src/model_config.py": ("29101440149241d293610a9a5c0c1b5b4e3ee08a", 3644, "442ad574094e7cd30e3ef9d911775ef2629c90bf9ecc084a83050b52dd29f8d0"),
    "src/orchestrator.py": ("54263af63702fcd146fbf884b2b2fe032b84e393", 87505, "f478e865e69545e2d567738a683ef0dec278d1d187c20198b11b59f9dcbc626b"),
    "src/production_executor.py": ("938f193b3871a1665730d5a3fd15c6301c3c37c7", 2134, "713003e4774cf0f7ec9e2916c37fe57e208f96ac5551da9a69668033771c4d7e"),
    "src/responses_adapter.py": ("7615435cac3400e053a07ddc081ad11f5e3990d7", 6499, "3b7ab2009e9a6342c19c219f3f21192147fc18ec0432838d985d8f1b0eab9f52"),
    "src/router_policy.py": ("a2a7a9b92f80c45846de5c0caf364df6d6296e32", 18133, "2cee69f0bc1f4e060a18bd17d3fc7202dde89956967c00adc739bccebf0fa131"),
    "src/run_history_consistency.py": ("53ddfe3d9dcddd6718d3899d1200e4c5d1d5ab81", 12291, "ae5b87d87d833bbb481bbb7df8bf43a9e38f6ce87e1a6cf10948d7082dbe1358"),
    "src/state_machine.py": ("f59dd4e2c6d6ede0dd5b9c8190e275ff1aadad06", 4983, "afe829049cff7cc32bb170ff41ce20debcaa80f8c0bc60a596c6b56b1162da6b"),
    "src/storage.py": ("674bb6929c1350ec9ffcc90564f89a2e610f3deb", 57944, "95b52f98466178365a53d209e7a3b2b98b5ed40d178b5683887d407e94300c88"),
}

