"""Plan B de event-redelivery (si LabRole no permite StartMessageMoveTask): mueve los mensajes de la DLQ a su cola."""
from common import aws, log


def handler(event, context):
    sqs = aws.client("sqs")
    moved = 0
    for _ in range(int(event.get("max_rounds", 50))):
        r = sqs.receive_message(QueueUrl=event["dlq_url"], MaxNumberOfMessages=10, WaitTimeSeconds=0,
                                VisibilityTimeout=60)
        msgs = r.get("Messages", [])
        if not msgs:
            break
        for m in msgs:
            sqs.send_message(QueueUrl=event["source_url"], MessageBody=m["Body"])
            sqs.delete_message(QueueUrl=event["dlq_url"], ReceiptHandle=m["ReceiptHandle"])
            moved += 1
    log.info("mensajes movidos desde la DLQ", moved=moved, dlq=event["dlq_url"])
    return {"moved": moved}
