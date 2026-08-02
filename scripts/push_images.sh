#!/usr/bin/env bash
# Push the Tier A training / serving images to one platform's registry.
#
# The three registries differ in login flow, and SageMaker additionally rejects
# the manifest format Docker 29's buildx produces by default. That difference
# used to live only in docs/runbooks/動作検証-sagemaker.md §3 as a hand-typed
# command, which meant the runbook was the only place the workaround existed.
#
#   usage: scripts/push_images.sh <vertex|sagemaker|azureml> [tag]
#
# exit code: 0=成功 / 1=push 失敗 / 2=引数・設定不備
set -euo pipefail

PLATFORM="${1:-}"
TAG="${2:-latest}"
OUTPUTS_DIR="artifacts"

case "$PLATFORM" in
  vertex|sagemaker|azureml) ;;
  *) echo "usage: $0 <vertex|sagemaker|azureml> [tag]" >&2; exit 2 ;;
esac

outputs() {  # outputs <env> <jq-filter>
  local file="$OUTPUTS_DIR/$1.outputs.json"
  [ -f "$file" ] || { echo "$file が無い。terraform output -json で作る" >&2; exit 2; }
  jq -r "$2" "$file"
}

REVISION="$(git rev-parse HEAD)"

case "$PLATFORM" in
  vertex)
    PREFIX="$(outputs gcp-dev '.container_image_prefix.value')"
    gcloud auth configure-docker "${PREFIX%%/*}" --quiet
    # orchestrator = Vertex AI Pipelines のステップの器（他基盤には不要）。
    # ローカルに無ければ黙って skip せず落とす（push 漏れ = パイプラインが exit 2 で死ぬ）
    for image in training serving orchestrator; do
      docker tag "mcml-$image:$TAG" "$PREFIX/$image:$TAG"
      docker push "$PREFIX/$image:$TAG"
    done
    ;;

  azureml)
    REGISTRY="$(outputs azure-dev '.container_registry_login_server.value')"
    az acr login --name "${REGISTRY%%.*}"
    for image in training serving; do
      docker tag "mcml-$image:$TAG" "$REGISTRY/$image:$TAG"
      docker push "$REGISTRY/$image:$TAG"
    done
    ;;

  sagemaker)
    TRAIN_REPO="$(outputs aws-dev '.ecr_repository_urls.value.training')"
    SERVE_REPO="$(outputs aws-dev '.ecr_repository_urls.value.serving')"
    aws ecr get-login-password | docker login --username AWS --password-stdin "${TRAIN_REPO%%/*}"

    docker tag "mcml-training:$TAG" "$TRAIN_REPO:$TAG"
    docker push "$TRAIN_REPO:$TAG"

    # ⚠️ serving は素の `docker push` だと ⑤ で落ちる（2026-08-01 実測）。
    # docker 29 の buildx は **OCI image index** で push するが、`CreateModel` は
    # `Unsupported manifest media type application/vnd.oci.image.index.v1+json` で拒否する。
    # **Training は OCI でも受理される**ため、②が通っても⑤で初めて表面化する。
    docker buildx build -f docker/serving/Dockerfile \
      --build-arg CODE_REVISION="$REVISION" \
      --provenance=false --sbom=false \
      --output "type=image,name=$SERVE_REPO:$TAG,oci-mediatypes=false,push=true" .

    # push しただけで満足しない。CreateModel が受ける形式かをここで確かめる。
    MEDIA_TYPE="$(aws ecr batch-get-image \
      --repository-name "$(basename "$SERVE_REPO")" \
      --image-ids "imageTag=$TAG" \
      --query 'images[].imageManifestMediaType' --output text)"
    EXPECTED="application/vnd.docker.distribution.manifest.v2+json"
    if [ "$MEDIA_TYPE" != "$EXPECTED" ]; then
      echo "serving の manifest 形式が CreateModel 非対応: $MEDIA_TYPE (期待: $EXPECTED)" >&2
      exit 1
    fi
    echo "serving manifest ok: $MEDIA_TYPE"
    ;;
esac
