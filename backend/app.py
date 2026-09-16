from flask import Flask
from flask import request
from flask import jsonify

from gam_copy import (
    copiar_configuracao
)

app = Flask(__name__)

@app.route("/health")
def health():

    return {
        "status": "ok"
    }

@app.route("/copy", methods=["POST"])
def copy():

    body = request.get_json()

    source_id = int(
        body["source_id"]
    )

    target_id = int(
        body["target_id"]
    )

    resultado = copiar_configuracao(
        source_id,
        target_id
    )

    return jsonify({

        "success": True,

        "message": resultado
    })

if __name__ == "__main__":

    app.run(
        host="0.0.0.0",
        port=8080,
        debug=True
    )