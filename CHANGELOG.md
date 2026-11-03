# Changelog

## [0.3.2](https://github.com/EffizienzAgent/datev_erpnext/compare/v0.3.1...v0.3.2) (2026-11-03)


### Bug Fixes

* support e-invoice tax rows and reverse charge over item tax templates ([#8](https://github.com/EffizienzAgent/datev_erpnext/issues/8)) ([995fb53](https://github.com/EffizienzAgent/datev_erpnext/commit/995fb538c19ce70e987187c1c02a3220b4f3188c))

## [0.3.1](https://github.com/EffizienzAgent/datev_erpnext/compare/v0.3.0...v0.3.1) (2026-11-03)


### Bug Fixes

* respect tax-key direction ([#6](https://github.com/EffizienzAgent/datev_erpnext/issues/6)) ([1c74db3](https://github.com/EffizienzAgent/datev_erpnext/commit/1c74db3385f0a1b9852b06ce5fc4bb5e73e20721))

## [0.3.0](https://github.com/EffizienzAgent/datev_erpnext/compare/v0.2.0...v0.3.0) (2026-11-03)


### Features

* v0.3 – payments and purchase invoices ([#5](https://github.com/EffizienzAgent/datev_erpnext/issues/5)) ([f9b7837](https://github.com/EffizienzAgent/datev_erpnext/commit/f9b7837b1bccbdf54b5542874413300b179cc9f7))


### Documentation

* drop local paths from plans, configurable core path in dev compose ([fe33ab2](https://github.com/EffizienzAgent/datev_erpnext/commit/fe33ab2f475468cb47997653cf70cc198cbbd760))

## [0.2.0](https://github.com/EffizienzAgent/datev_erpnext/compare/v0.1.0...v0.2.0) (2026-11-02)


### Features

* v0.2 – strict tax checks, catch-up, report, shorter locks ([#2](https://github.com/EffizienzAgent/datev_erpnext/issues/2)) ([65f78d0](https://github.com/EffizienzAgent/datev_erpnext/commit/65f78d035c4fc1b250286e5df7318d37509383a7))

## [0.1.0](https://github.com/EffizienzAgent/datev_erpnext/compare/v0.1.0...v0.1.0) (2026-11-02)


### Features

* add settings and company client lookup ([de88cda](https://github.com/EffizienzAgent/datev_erpnext/commit/de88cda9833b2b7197234c642e94917298353b40))
* block cancelling invoices that are already in DATEV ([5feb023](https://github.com/EffizienzAgent/datev_erpnext/commit/5feb023935110bbf00f1f50a7dacada07d472041))
* build DATEV booking lines from sales invoices ([6e0d6fd](https://github.com/EffizienzAgent/datev_erpnext/commit/6e0d6fd909378c31be6fa9b440206ef90a5e9ff2))
* map customers to DATEV party accounts ([5a3dbd0](https://github.com/EffizienzAgent/datev_erpnext/commit/5a3dbd0d8f99d12e03c06fc334e7938c2ee4bbc9))
* render the document image for sales invoices ([b542ee4](https://github.com/EffizienzAgent/datev_erpnext/commit/b542ee418c2a1d2e6906c61caff6645abcd787b7))
* sync submitted sales invoices to the datev app ([b5c779f](https://github.com/EffizienzAgent/datev_erpnext/commit/b5c779f2482592ec1a39083dfafb4ce833d719ff))


### Bug Fixes

* book booking lines side-aware and reject unsupported charges ([54a0128](https://github.com/EffizienzAgent/datev_erpnext/commit/54a0128429ebd23ecfc18183409ce452dfdd4053))
* comment on invoice when sync cannot be queued, polish docs and headers ([2224a0d](https://github.com/EffizienzAgent/datev_erpnext/commit/2224a0d638ea5a1ab0c55ff6d9334053f5714aec))
* harden invoice sync before first deployment ([8cc5455](https://github.com/EffizienzAgent/datev_erpnext/commit/8cc54552fd232e5373b11468026c251805787052))
* roll back failed datev syncs fully and harden sync entry points ([382e6ee](https://github.com/EffizienzAgent/datev_erpnext/commit/382e6ee0506f252497c80b4829ac7450a8d4a5e3))


### Documentation

* add German translations, README and docs ([c4fd7a1](https://github.com/EffizienzAgent/datev_erpnext/commit/c4fd7a1de7ba19846a8d3baaba69782f7ff55745))
* document sync from date, failure comments and go-live order ([e5f2b44](https://github.com/EffizienzAgent/datev_erpnext/commit/e5f2b442c264a8d7fc46f0965ccba0b2006c1590))


### Miscellaneous Chores

* release 0.1.0 ([6878fbe](https://github.com/EffizienzAgent/datev_erpnext/commit/6878fbe318752887800ec7f11cdea860e2b74369))
