# Changelog

## [0.12.0](https://github.com/dantuck/pkb-cli/compare/v0.11.0...v0.12.0) (2026-10-30)


### Features

* **web:** make the phone page an app with a settings shelf, grouped TODOs and offline use ([8a615dc](https://github.com/dantuck/pkb-cli/commit/8a615dc1d897ce75e2f776de7a6e8226ca92ffd8))

## [0.11.0](https://github.com/dantuck/pkb-cli/compare/v0.10.0...v0.11.0) (2026-10-29)


### Features

* **remind:** show TODO titles in the phone notification ([2be8a54](https://github.com/dantuck/pkb-cli/commit/2be8a5442986176b535378b31304764b654d9960))

## [0.10.0](https://github.com/dantuck/pkb-cli/compare/v0.9.1...v0.10.0) (2026-10-29)


### Features

* **remind:** manage todos from the phone, devices from the computer ([6f5e7f3](https://github.com/dantuck/pkb-cli/commit/6f5e7f313fffb522e19a8bc52318c819356c4620))


### Bug Fixes

* **web:** make the todo panel scroll and set an exact due time ([fdf8681](https://github.com/dantuck/pkb-cli/commit/fdf8681d5ffd76982352e8e87d684cd296e358f8))

## [0.9.1](https://github.com/dantuck/pkb-cli/compare/v0.9.0...v0.9.1) (2026-10-29)


### Bug Fixes

* **remind:** prune stale files when refreshing the copied-out Worker ([902c86f](https://github.com/dantuck/pkb-cli/commit/902c86f0f269868c1dde85c4c7bdec7ebb2972b4))

## [0.9.0](https://github.com/dantuck/pkb-cli/compare/v0.8.0...v0.9.0) (2026-10-29)


### Features

* **remind:** proper notification icon and badge on the phone ([d4b14bd](https://github.com/dantuck/pkb-cli/commit/d4b14bd85b9434a5109530a8d881f37e61b7411e))

## [0.8.0](https://github.com/dantuck/pkb-cli/compare/v0.7.0...v0.8.0) (2026-10-28)


### Features

* **remind:** deploy and update the Worker from kb ([c8bb316](https://github.com/dantuck/pkb-cli/commit/c8bb3160caf1a70c54b4d9783addee0d248aa23d))
* **remind:** ship the Worker in the package; add kb remind push init ([128c336](https://github.com/dantuck/pkb-cli/commit/128c3360924b3208fe914fa7fe107b04b69ceb8e))


### Bug Fixes

* **remind:** reject --ask outside kb remind push deploy ([c31feec](https://github.com/dantuck/pkb-cli/commit/c31feec59b20f80d4487c2019b8fa20f9a8dd1ee))


### Documentation

* deploy and upgrade the phone-reminder Worker from the package ([8ef19bc](https://github.com/dantuck/pkb-cli/commit/8ef19bca2ee2792e873c42fe0561d919831f075d))
* document deploying and updating the Worker with kb ([3ce21eb](https://github.com/dantuck/pkb-cli/commit/3ce21eb963c824e5b8ebb6c92d4f879a672994cf))

## [0.7.0](https://github.com/dantuck/pkb-cli/compare/v0.6.1...v0.7.0) (2026-10-28)


### Features

* **remind:** push due TODOs to your phone via a Cloudflare Worker ([040da12](https://github.com/dantuck/pkb-cli/commit/040da1281580ebbce061d77e6ab7b60f7ea27f37))
* **web:** sort todos by due date within each priority ([f71ce59](https://github.com/dantuck/pkb-cli/commit/f71ce5952a3aa1271b32c94fd67d2024df1b35fe))


### Documentation

* document phone reminders ([df0a762](https://github.com/dantuck/pkb-cli/commit/df0a7622b7897b58d8da5ac30e62589ed3a76dae))

## [0.6.1](https://github.com/dantuck/pkb-cli/compare/v0.6.0...v0.6.1) (2026-10-21)


### Bug Fixes

* **web:** size and wrap setup output in Manage panel ([4a35237](https://github.com/dantuck/pkb-cli/commit/4a3523737af0f09869ce5d906a79c17d2a86fe61))

## [0.6.0](https://github.com/dantuck/pkb-cli/compare/v0.5.0...v0.6.0) (2026-10-21)


### Features

* **search:** add optional semantic search and --json output ([5c0ea1b](https://github.com/dantuck/pkb-cli/commit/5c0ea1b997c27393295e341d6eb11e32e3d575ae))

## [0.5.0](https://github.com/dantuck/pkb-cli/compare/v0.4.0...v0.5.0) (2026-10-21)


### Features

* **web:** confirm before closing a todo from its row X ([5d3e97b](https://github.com/dantuck/pkb-cli/commit/5d3e97b445c12f4f2b03de8a2d01c86253fe80ae))

## [0.4.0](https://github.com/dantuck/pkb-cli/compare/v0.3.0...v0.4.0) (2026-10-21)


### Features

* **remind:** show reminders with kb's own icon and guide permission ([c61a92d](https://github.com/dantuck/pkb-cli/commit/c61a92d15cf83e00a9c1bd9d600504fcbd625e7b))
* **todo:** add due dates, snooze and desktop reminders ([4ee765f](https://github.com/dantuck/pkb-cli/commit/4ee765fa433b1f118208e86bbbefd782f5e463a4))
* **web:** add reminder and snooze controls to the todo panel ([f2cde84](https://github.com/dantuck/pkb-cli/commit/f2cde84bb2f63255dd29408798ba756098624eba))
* **web:** add Reminders card, Manage rename and loading spinner ([c74ac37](https://github.com/dantuck/pkb-cli/commit/c74ac3749db596f0148f37bd7924215d7c356a2e))


### Bug Fixes

* **service:** retry launchd bootstrap after bootout ([3bc737d](https://github.com/dantuck/pkb-cli/commit/3bc737de8a4456515a44848a73c6e633589ebcb1))

## [0.3.0](https://github.com/dantuck/pkb-cli/compare/v0.2.0...v0.3.0) (2026-10-03)


### Features

* **web:** rebuild admin panel to manage kb end to end ([b70f880](https://github.com/dantuck/pkb-cli/commit/b70f880171430dec7f37b374fc0dbb3be5765a93))

## [0.2.0](https://github.com/dantuck/pkb-cli/compare/v0.1.2...v0.2.0) (2026-10-03)


### Features

* **update:** refresh installed services after a reinstall ([5cc5cbe](https://github.com/dantuck/pkb-cli/commit/5cc5cbe8b6abcf87e84fd7e5cd8c505dfccd12bc))

## [0.1.2](https://github.com/dantuck/pkb-cli/compare/v0.1.1...v0.1.2) (2026-10-03)


### Bug Fixes

* **update:** reinstall by tag instead of running upgrade ([05de5f1](https://github.com/dantuck/pkb-cli/commit/05de5f18ccec6dfde248f5bae5af5bbab129422c))

## [0.1.1](https://github.com/dantuck/pkb-cli/compare/v0.1.0...v0.1.1) (2026-10-03)


### Bug Fixes

* drop component prefix from release tags ([3c21c95](https://github.com/dantuck/pkb-cli/commit/3c21c95a9c356f2a157d92e6cc6a103f7ff8974e))

## 0.1.0 (2026-10-03)


### Features

* **doctor:** flag promoted entries stale relative to their source ([56f3197](https://github.com/dantuck/pkb-cli/commit/56f3197cfc417ba9033f98e281dbd231bb118691))
* **doctor:** validate config.yml keys and value types ([8708c20](https://github.com/dantuck/pkb-cli/commit/8708c2025eaace6ecd2300b42749fdee3ffb65a6))
* install and update pkb-cli without requiring git ([fe0f44d](https://github.com/dantuck/pkb-cli/commit/fe0f44d8c542b2af785890bbadb8bd3dccb98d12))
* **journal:** add on-this-day surfacing, monthly rollups, and tag browsing ([ab92f75](https://github.com/dantuck/pkb-cli/commit/ab92f75acc4821631b2a43cafeee89d858f66fc7))
* **kb:** add interactive picker and quick-add to `kb journal` ([ac0a4a7](https://github.com/dantuck/pkb-cli/commit/ac0a4a733dd1432ed0921cdfbe9d59bcb937c7af))
* **kb:** add kb rm and kb mv commands ([b947e4d](https://github.com/dantuck/pkb-cli/commit/b947e4dc7ee8b5a4a2a9daf0a3c67aa17a4551b6))
* **kb:** add tag/link/show commands and json output modes ([27d6bbc](https://github.com/dantuck/pkb-cli/commit/27d6bbc5043988b28459a67feb6d2a6ec0f62c90))
* **kb:** add todo update, delete, and dependency management ([4b27f8d](https://github.com/dantuck/pkb-cli/commit/4b27f8dba339b224596b644ac0c1a519b2686cc7))
* **kb:** auto-commit, admin health checks, and a push flow ([d0dc671](https://github.com/dantuck/pkb-cli/commit/d0dc67131010fb1eac82a041a5eb577163c161c4))
* **kb:** let kb remember a preferred editor via `kb config editor` ([f40b08a](https://github.com/dantuck/pkb-cli/commit/f40b08a375b1e954d40a655d6681731e5178156f))
* **kb:** mark synced source mirrors read-only ([4b229bf](https://github.com/dantuck/pkb-cli/commit/4b229bf0cb13e97f5016bda31cadd830bfeaa4cd))
* **kb:** resolve inbox items non-interactively, add json to triage ([f5cf70d](https://github.com/dantuck/pkb-cli/commit/f5cf70d45af15b93f5a44a032c425accb00acb84))
* **kb:** share entry creation between CLI and web API ([0915362](https://github.com/dantuck/pkb-cli/commit/0915362fd045e2747e6965a8ffa3ec4ccdf8c7de))
* **kb:** support editing entry title/body with safe concurrent writes ([1ac0a8b](https://github.com/dantuck/pkb-cli/commit/1ac0a8b029d29eb1a19a68ed56439a333deba372))
* **search:** auto-prefix bare words in kb search queries ([619f3f9](https://github.com/dantuck/pkb-cli/commit/619f3f93c0eb594d17116bea939f1d6f47b869f2))
* **setup:** add --install-deps, fix stale index after sync, fix service PATH ([3548670](https://github.com/dantuck/pkb-cli/commit/3548670b581107164e891f953bbd7cdc084d344a))
* **setup:** bootstrap ~/.pkb and make setup a guided flow ([f8834a5](https://github.com/dantuck/pkb-cli/commit/f8834a5cf5285bd318a4821de2d5fd4d15f81c95))
* ship kb as an installable package ([a61beb5](https://github.com/dantuck/pkb-cli/commit/a61beb5f1b548e38655bc0910deda261520a930d))
* **skill:** bundle a Claude Code skill and an installer for it ([3f9d880](https://github.com/dantuck/pkb-cli/commit/3f9d880b771caeb62af96dd89d9dcc9846b97641))
* **sync:** add GitHub issue sync, auto-discover pluggable sync sources ([ac969a9](https://github.com/dantuck/pkb-cli/commit/ac969a9585f5be641a925914f898ed24d712752d))
* **sync:** add kb sync-service for recurring sync via launchd/systemd ([f2aa135](https://github.com/dantuck/pkb-cli/commit/f2aa135b6354669115001931d4b63ea71a84597b))
* **sync:** download usememos attachments and render them in kb web ([a001b9d](https://github.com/dantuck/pkb-cli/commit/a001b9d51e85668300ed6dcd7084ceb8cf1291c1))
* **web:** add 'kb open' alias for 'kb web' ([c51da44](https://github.com/dantuck/pkb-cli/commit/c51da448632801748b81314d9da82ea35f847d09))
* **web:** add `kb service` to run the web UI as a login service ([6134b00](https://github.com/dantuck/pkb-cli/commit/6134b00acf681990e46468177a4d2b71f0967722))
* **web:** add kb web -- local browser UI for feed, capture, and triage ([5088646](https://github.com/dantuck/pkb-cli/commit/5088646cfbdccf4a7ac413d08331bae7ec214787))
* **web:** add lightbox for post images and videos ([16e41d6](https://github.com/dantuck/pkb-cli/commit/16e41d6f3c813fbbc6b14ebc9c363e320869c384))
* **web:** add new entry form to the web ui ([902f48a](https://github.com/dantuck/pkb-cli/commit/902f48a8ac29552f504aafd819eaeaf4df191553))
* **web:** collapse capture box on scroll and refresh styling ([f705e60](https://github.com/dantuck/pkb-cli/commit/f705e6084723de15ef5845e99518b09a6561c93a))
* **web:** open entries in a standalone modal for viewing and editing ([cb2be08](https://github.com/dantuck/pkb-cli/commit/cb2be0823973722e2478ff4ba514c2668a9acc3d))
* **web:** open the running instance when the port is already in use ([e377cfc](https://github.com/dantuck/pkb-cli/commit/e377cfc08e0f084a7974db5f4857a31bf17e0d14))
* **web:** pin drawer headers while their lists scroll ([0cf204d](https://github.com/dantuck/pkb-cli/commit/0cf204d8f016bef0d369bf259155c4b770e7ef17))
* **web:** pin the header so content scrolls beneath it ([1d90af3](https://github.com/dantuck/pkb-cli/commit/1d90af3a26852cb7d78dc64b709062806cef5007))
* **web:** play synced videos inline and grid multi-media posts ([0b6b097](https://github.com/dantuck/pkb-cli/commit/0b6b097bae34a05082dbf4fbf77dd511c32ad8d5))
* **web:** push live updates to open tabs via SSE ([4efe0e9](https://github.com/dantuck/pkb-cli/commit/4efe0e9f383aa55521754a85400497ec96902f1c))
* **web:** rewrite kb web ui with alpine.js ([b70c336](https://github.com/dantuck/pkb-cli/commit/b70c33607852fb26beb248ec9ad4043bc71e9e19))


### Bug Fixes

* clear lint errors from the package move ([16635dd](https://github.com/dantuck/pkb-cli/commit/16635dd60b4a8b964d7c6e4668a4c4f068a97f68))
* **config:** merge partial config.yml overrides instead of replacing defaults ([e9dd6a9](https://github.com/dantuck/pkb-cli/commit/e9dd6a992629f5de0a7dd4b3a9e5d87db8a199ab))
* harden the tarball install/update path against corruption and reduce duplication ([334502b](https://github.com/dantuck/pkb-cli/commit/334502bfbdb3da6367bd65efa59c3f2d870f39a7))
* **journal:** auto-reindex after appending a note ([10ee7dc](https://github.com/dantuck/pkb-cli/commit/10ee7dc641e69b04585af00840a0c3d2218bee99))
* kb setup --install and kb doctor required a data repo unnecessarily ([7e713b4](https://github.com/dantuck/pkb-cli/commit/7e713b40adca4f4bfc53c4089236ca7f86b99cef))
* **kb:** give unknown command a readable error with a suggestion ([21e8a23](https://github.com/dantuck/pkb-cli/commit/21e8a23e3a438e7e13d5d74498df2c93829422b6))
* **kb:** rebuild memo mirror body when rendered content changes ([2937cb1](https://github.com/dantuck/pkb-cli/commit/2937cb154f99c2aa32ec2a6f8f60834fadd19c18))
* **kb:** stop entry_update_content silently discarding new body ([7daa1cd](https://github.com/dantuck/pkb-cli/commit/7daa1cd9ae00a1d74a9cf5e4d7639029ca42a2c7))
* **new:** auto-index entries created by kb new ([3ceafbc](https://github.com/dantuck/pkb-cli/commit/3ceafbc67af934074a637f9fd6c0cea1dc97646f))
* **sync:** allow disabling the memos inbox stub entirely ([a906459](https://github.com/dantuck/pkb-cli/commit/a9064592e769e314efb650374e7f6d2ff59ecf4c))
* **sync:** let memo mirrors hit the unchanged fast path ([b456052](https://github.com/dantuck/pkb-cli/commit/b456052e46437aafcfeb4e7a7d3b73be64271eda))
* **sync:** stop beads sync from flooding the inbox by default ([6e251c3](https://github.com/dantuck/pkb-cli/commit/6e251c325acddd599fe197301c9ef8273f9aa4af))
* **test:** pin the test repo's branch name in push flow tests ([5c0109b](https://github.com/dantuck/pkb-cli/commit/5c0109b1a3ebc83c2a7c1f0d50c23ffe04f86832))
* **web:** exclude beads items from the feed ([f0ed8b0](https://github.com/dantuck/pkb-cli/commit/f0ed8b0b0407af1cf226d39d699f65494cb39fe9))
* **web:** normalize request path before content-dir check ([df62fa0](https://github.com/dantuck/pkb-cli/commit/df62fa0e9992ee6c9736763f16cf630819879bdf))
* **web:** surface kb index/validate/sync output in the web UI ([4ceddb5](https://github.com/dantuck/pkb-cli/commit/4ceddb5c341c08890b255207f51c27afd075e5f9))


### Documentation

* add config reference, document read-only mirrors and web media ([65f49ef](https://github.com/dantuck/pkb-cli/commit/65f49ef7afa0ceb77a88536b4fd8324ea95aae0a))
* clarify --install target dir and document from-scratch data repo setup ([a2b24ea](https://github.com/dantuck/pkb-cli/commit/a2b24eabdb76c725f1428fcb54d522bffa2e2b33))
* document new kb commands in README and the bundled skill ([00498f9](https://github.com/dantuck/pkb-cli/commit/00498f917526d418bbb417cfc53b75d76f195504))
* document the package install and release flow ([ddb9c53](https://github.com/dantuck/pkb-cli/commit/ddb9c5309e171acd1ffaa85cd8e38f5e09b15d21))
* note that pkb-cli's own notes/todos live in the kb CLI ([34d6878](https://github.com/dantuck/pkb-cli/commit/34d6878b1f045e19785be2d171bfee73d6e05e94))
* restructure docs into diataxis tutorials/how-to/reference/explanation ([a69930a](https://github.com/dantuck/pkb-cli/commit/a69930ad88bd89be8151468ce37d6ecbbb9a25e2))
* **scripts:** repoint sync_memos ingestion contract link to new docs ([94e87ed](https://github.com/dantuck/pkb-cli/commit/94e87ed1ba6bce79a50d38415edb64336c950db6))
* **skill:** clarify which kb commands auto-reindex ([ce7c54f](https://github.com/dantuck/pkb-cli/commit/ce7c54f120d557b016e783c96f227ea99585e01e))
* **web:** document `kb service` install/uninstall/status ([2765d96](https://github.com/dantuck/pkb-cli/commit/2765d96a24a1b6d83a86e9d73e9e94e162f7e13c))
