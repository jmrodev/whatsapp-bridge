FROM golang:1-alpine AS builder

RUN apk add --no-cache gcc musl-dev sqlite-dev

WORKDIR /app

COPY go.mod go.sum ./
RUN go mod download

COPY . .
RUN go mod tidy

RUN go build -o whatsapp-bridge main.go

FROM alpine:latest

RUN apk add --no-cache sqlite-libs tzdata

WORKDIR /app

COPY --from=builder /app/whatsapp-bridge .

EXPOSE 8090

CMD ["./whatsapp-bridge"]
