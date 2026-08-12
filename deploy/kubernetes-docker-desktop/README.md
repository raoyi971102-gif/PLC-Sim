# PLC-Sim on Docker Desktop Kubernetes

This source-based deployment runs PLC-Sim in GUI-managed mode in the `app`
namespace. The GUI process stays resident and starts or stops the OPC UA server
and SZLab handshake agent only when requested from the browser.

Build the local image, publish it through a temporary loopback registry for the
Docker Desktop Kubernetes node, and apply the manifest:

```bash
docker build -t plc-sim:docker-desktop .
docker run --rm -d --name plc-sim-image-registry -p 5050:5000 registry:2
docker tag plc-sim:docker-desktop localhost:5050/plc-sim:docker-desktop
docker push localhost:5050/plc-sim:docker-desktop
kubectl apply -f deploy/kubernetes-docker-desktop/plc-sim.yaml
```

After the Pod has pulled the image, the temporary registry may be stopped. The
node keeps the image in its local cache because the manifest uses
`imagePullPolicy: IfNotPresent`.

Host access:

- Web GUI: <http://localhost:18765>
- OPC UA endpoint after clicking **Start Server** in the GUI:
  `opc.tcp://localhost:4855/xuse_sim/`

Workloads in the `app` namespace use
`opc.tcp://plc-sim:4855/xuse_sim/`. Uploaded CSV files and runtime state are
stored on the `plc-sim-data` PVC.
