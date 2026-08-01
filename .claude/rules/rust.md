---
paths:
  - "**/*.rs"
  - "**/Cargo.toml"
---

# Rust ルール

- 通常 API は axum を第一候補にする。CLI / batch も Rust を優先する。
- 挙動を変えたら `cargo fmt`、`cargo clippy -- -D warnings`、`cargo test` を実際に走らせて確認する（推測で緑にしない）。
- `unwrap()` / `expect()` / `panic!` は、失敗が本当に到達不能な場所か test 内だけにする。ライブラリ経路の誤りは `anyhow::Result` / `thiserror` で返す。
- 小さな Clean Architecture（domain / application / infrastructure / interface）を保つ。trait Port は**複数実装が必要になった境界にだけ**足す（先回りの抽象化をしない）。
- 公開 API・CLI 引数・エラー型を変えたら、同じ変更内で docs とテストも直す（drift を作らない）。
