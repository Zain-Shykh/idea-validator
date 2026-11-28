# Packs one GGUF into an image, so the kubelet pulls the model and no pod needs internet egress
# (ADR-005). The build context is a folder holding only model.gguf; see README.md.
FROM alpine:3@sha256:294b683cb724975bec92580e1e685676bd4b50bda910ddb8c51d4cabeaec77e6 AS checksum
COPY model.gguf /model/model.gguf
RUN cd /model && sha256sum model.gguf > model.gguf.sha256

# separate stage: the RUN above must not add a second copy of the model to the image
FROM alpine:3@sha256:294b683cb724975bec92580e1e685676bd4b50bda910ddb8c51d4cabeaec77e6
# the mode also lands on the new /model folder, so it needs the x bit or uid 10001 cannot enter it
COPY --chmod=0555 model.gguf /model/model.gguf
COPY --from=checksum /model/model.gguf.sha256 /model/model.gguf.sha256
USER 10001
