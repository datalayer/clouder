# Examples

```bash
kubectl get nodes
kubectl get nodepool
kubectl get all -A
```

## Pod

```bash
kubectl run debian --image=debian --restart=Never -- sleep 1000
sleep 10 # wait on image pull.
kubectl get pod debian
kubectl exec debian -it -- sh
# exit
kubectl delete pod debian
```

```bash
kubectl run fedora --image=fedora --restart=Never -- sleep 1000
sleep 10 # wait on image pull.
kubectl get pod fedora
kubectl exec fedora -it -- sh
# exit
kubectl delete pod fedora
```

```bash
kubectl run alpine --image=alpine --restart=Never -- sleep 1000
sleep 10 # wait on image pull.
kubectl get pod alpine
kubectl exec alpine -it -- sh
# exit
kubectl delete pod alpine
```

```bash
cat << EOF | kubectl apply -f -
apiVersion: v1
kind: Pod
metadata:
  name: envar-demo
  labels:
    purpose: demonstrate-envars
spec:
  containers:
  - name: envar-demo-container
    image: gcr.io/google-samples/node-hello:1.0
    command: [ "/bin/sh", "-c", "--" ]
    args: [ "while true; do sleep 300; done;" ]    
    env:
    - name: DEMO_GREETING
      value: "Hello from the environment"
EOF
kubectl get pods -l purpose=demonstrate-envars
kubectl exec -it envar-demo -- /bin/bash
# echo $DEMO_GREETING
# exit
kubectl delete pod envar-demo
```

## Load Balancer

```bash
kubectl apply -f ./manifests/svc-lb.yml
kubectl get svc hello-world -w
export LOAD_BALANCER_IP=$(kubectl get svc hello-world -n default -o jsonpath='{.status.loadBalancer.ingress[].ip}')
open http://$LOAD_BALANCER_IP
kubectl delete -f ./manifests/svc-lb.yml
```

## Ingress Nginx

```bash
helm repo add ingress-nginx https://kubernetes.github.io/ingress-nginx
helm repo update
```

```bash
helm upgrade \
  --install ingress-nginx \
  ingress-nginx/ingress-nginx \
  -n ingress-nginx \
  --create-namespace \
  --version 4.0.13
helm ls -n ingress-nginx
kubectl get deployment ingress-nginx-controller -n ingress-nginx
kubectl get service ingress-nginx-controller -n ingress-nginx -w
export INGRESS_NGINX_IP=$(kubectl get service ingress-nginx-controller -n ingress-nginx -o json | jq -r '.status.loadBalancer.ingress[].ip')
echo $INGRESS_NGINX_IP
curl http://$INGRESS_NGINX_IP # Should retrun 404 page.
open http://$INGRESS_NGINX_IP # Should retrun 404 page.
# kubectl create deployment hello-app --image=gcr.io/google-samples/hello-app:1.0
# kubectl expose deployment hello-app --port=8080 --target-port=8080
cat << EOF | kubectl apply -f -
apiVersion: apps/v1
kind: Deployment
metadata:
  name: hello-app
  namespace: default
  labels:
    app: hello-app
spec:
  replicas: 3
  selector:
    matchLabels:
      app: hello-app
  template:
    metadata:
      labels:
        app: hello-app
    spec:
      containers:
      - name: hello-app
        image: gcr.io/google-samples/hello-app:1.0
        imagePullPolicy: IfNotPresent
        ports:
          - containerPort: 8080
---
apiVersion: v1
kind: Service
metadata:
  name: hello-app
  labels:
    app: hello-app
spec:
  ports:
  - port: 8080
    targetPort: 8080
    protocol: TCP
    name: http
  selector:
    app: hello-app
EOF
kubectl get deployment
kubectl get svc
cat <<EOF | kubectl apply -f -
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: ingress-resource
  annotations:
    kubernetes.io/ingress.class: "nginx"
    nginx.ingress.kubernetes.io/ssl-redirect: "false"
    nginx.ingress.kubernetes.io/affinity: "cookie"
    nginx.ingress.kubernetes.io/session-cookie-name: "route"
    nginx.ingress.kubernetes.io/session-cookie-expires: "172800"
    nginx.ingress.kubernetes.io/session-cookie-max-age: "172800"
spec:
  rules:
  - host: ${INGRESS_NGINX_IP}.nip.io
    http:
      paths:
      - path: /hello
        pathType: Prefix
        backend:
          service:
            name: hello-app
            port:
              number: 8080
EOF
kubectl get ingress ingress-resource
curl http://$INGRESS_NGINX_IP.nip.io/hello
open http://$INGRESS_NGINX_IP.nip.io/hello
cat <<EOF | kubectl apply -f -
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: ingress-resource-2
  annotations:
    kubernetes.io/ingress.class: "nginx"
    nginx.ingress.kubernetes.io/ssl-redirect: "false"
    nginx.ingress.kubernetes.io/affinity: "cookie"
    nginx.ingress.kubernetes.io/session-cookie-name: "route"
    nginx.ingress.kubernetes.io/session-cookie-expires: "172800"
    nginx.ingress.kubernetes.io/session-cookie-max-age: "172800"
spec:
  rules:
  - host: ${INGRESS_NGINX_IP}.nip.io
    http:
      paths:
      - path: /hello2
        pathType: Prefix
        backend:
          service:
            name: hello-app
            port:
              number: 8080
EOF
kubectl get ingress ingress-resource-2
curl http://$INGRESS_NGINX_IP.nip.io/hello2
open http://$INGRESS_NGINX_IP.nip.io/hello2
kubectl delete ingress ingress-resource-2
kubectl delete ingress ingress-resource
kubectl delete svc hello-app
kubectl delete deployment hello-app
helm uninstall -n ingress-nginx ingress-nginx
```

## Certificate Manager

```bash
helm repo add jetstack https://charts.jetstack.io
helm repo update
```

```bash
helm install \
  cert-manager \
  jetstack/cert-manager \
  --version v1.10.1 \
  --namespace cert-manager \
  --create-namespace \
  --set installCRDs=true
helm ls -n cert-manager
kubectl get pods -n cert-manager
```

```bash
cat <<EOF | kubectl apply -f -
apiVersion: cert-manager.io/v1
kind: ClusterIssuer
metadata:
  name: letsencrypt-prod
spec:
  acme:
    server: https://acme-v02.api.letsencrypt.org/directory
    preferredChain: "ISRG Root X1"
    email: eric@datalayer.io
    privateKeySecretRef:
      name: letsencrypt-prod
    solvers:
    - http01:
        ingress:
            class: nginx
EOF
kubectl describe clusterissuer letsencrypt-prod
```

## Simple Service

```bash
cat << EOF | kubectl apply -f -
apiVersion: v1
kind: Namespace
metadata:
  name: datalayer-simple
---
apiVersion: v1
kind: Secret
metadata:
  name: simple-secret
  namespace: datalayer-simple
  labels:
    app: simple
data:
  username: ZmFrZQ== # fake base64 encoded string
  password: ZmFrZQ== # fake base64 encoded string
  DATALAYER_LDAP_BIND_PWD: ZmFrZQ== # fake base64 encoded string
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: simple
  namespace: datalayer-simple
  labels:
    app: simple
spec:
  replicas: 3
  selector:
    matchLabels:
      app: simple
  template:
    metadata:
      labels:
        app: simple
    spec:
      containers:
        - name: simple
          image: datalayer/simple:0.0.6
          securityContext:
            runAsUser: 0
            privileged: true
          ports:
            - containerPort: 9876
              protocol: TCP
          env:
          - name: "USERNAME"
            valueFrom:
              secretKeyRef:
                name: simple-secret
                key: username
          - name: "DATALAYER_LDAP_BIND_PWD"
            valueFrom:
              secretKeyRef:
                name: simple-secret
                key: DATALAYER_LDAP_BIND_PWD
---
apiVersion: v1
kind: Service
metadata:
  name: simple-svc
  namespace: datalayer-simple
spec:
  type: ClusterIP
  ports:
  - port: 9876
    targetPort: 9876
    protocol: TCP
  selector:
    app: simple
---
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: simple-ingress
  namespace: datalayer-simple
  annotations:
    kubernetes.io/ingress.class: 'nginx'
#    nginx.ingress.kubernetes.io/rewrite-target: '/'
    nginx.ingress.kubernetes.io/ssl-redirect: 'true'
    nginx.ingress.kubernetes.io/force-ssl-redirect: 'true'
    nginx.ingress.kubernetes.io/affinity: cookie
    cert-manager.io/acme-challenge-type: 'http01'
    cert-manager.io/cluster-issuer: 'letsencrypt-prod'
spec:
  tls:
  - hosts:
    - simple-dev.datalayer.community
    secretName: simple-dev-datalayer-community-cert
  rules:
  - host: simple-dev.datalayer.community
    http:
      paths:
        - path: /
          pathType: Prefix
          backend:
            service:
              name: simple-svc
              port:
                number: 9876
EOF
kubectl get pods -n datalayer-simple
kubectl get secrets -n datalayer-simple
kubectl describe secrets -n datalayer-simple simple-secret
POD_NAME=$(kubectl get pods -n datalayer-simple -l "app=simple-echo-2" -o jsonpath="{.items[0].metadata.name}")
echo $POD_NAME
kubectl exec $POD_NAME -n datalayer-simple -it -- echo $DATALAYER_LDAP_BIND_PWD
kubectl exec $POD_NAME -n datalayer-simple -it -- /bin/bash
kubectl get certificates -n datalayer-simple
kubectl describe certificate simple-dev-datalayer-community-cert -n datalayer-simple
kubectl describe certificaterequest -n datalayer-simple simple-dev-datalayer-community-cert-s5ph...
curl https://simple-dev.datalayer.community/info
# {"version": "0.0.3", "host": "simple-dev.datalayer.community", "from": "10.44.0.2", "local_hostname": "simple-64c4fd859f-p9w74", "local_ip": "10.44.0.6", "headers": [{"Host": "simple-dev.datalayer.community"}, {"X-Request-Id": "f76ad6945e9ebc3ba9ea8da3a6ea2adb"}, {"X-Real-Ip": "10.44.0.0"}, {"X-Forwarded-For": "10.44.0.0"}, {"X-Forwarded-Host": "simple-dev.datalayer.community"}, {"X-Forwarded-Port": "443"}, {"X-Forwarded-Proto": "https"}, {"X-Forwarded-Scheme": "https"}, {"X-Scheme": "https"}, {"User-Agent": "curl/7.64.1"}, {"Accept": "*/*"}]}
```

## Dashboard

```bash
kubectl apply -f https://raw.githubusercontent.com/kubernetes/dashboard/v2.7.0/aio/deploy/recommended.yaml
cat << EOF | kubectl apply -f -
apiVersion: v1
kind: ServiceAccount
metadata:
  name: admin-user
  namespace: kubernetes-dashboard
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: admin-user
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: cluster-admin
subjects:
- kind: ServiceAccount
  name: admin-user
  namespace: kubernetes-dashboard
---
apiVersion: v1
kind: Secret
type: kubernetes.io/service-account-token
metadata:
  name: admin-user-token
  namespace: kubernetes-dashboard
  annotations:
    kubernetes.io/service-account.name: admin-user
EOF
kubectl -n kubernetes-dashboard describe secret $(kubectl -n kubernetes-dashboard get secret | grep admin-user-token | awk '{print $1}')
kubectl proxy
open http://localhost:8001/api/v1/namespaces/kubernetes-dashboard/services/https:kubernetes-dashboard:/proxy/
```

## Volume

```bash
kubectl get sc
kubectl describe sc default
kubectl create ns nginx-example
kubectl apply -f ./manifests/pvc.yml
kubectl get pvc -n nginx-example
kubectl get pv -n nginx-example
kubectl apply -f ./manifests/deployment-nginx.yml
kubectl get deployment nginx-deployment -n nginx-example
kubectl apply -f ./manifests/svc-nginx.yml
kubectl -n nginx-example get svc/nginx-service -w
export INGRESS_NGINX_IP=$(kubectl get svc nginx-service -n nginx-example -o jsonpath='{.status.loadBalancer.ingress[].ip}')
echo $INGRESS_NGINX_IP
curl -I http://$INGRESS_NGINX_IP
open http://$INGRESS_NGINX_IP
export POD_NAME=$(kubectl get po -n nginx-example -o name)
kubectl -n nginx-example exec $POD_NAME -c nginx -- cat /var/log/nginx/access.log
kubectl -n nginx-example describe $POD_NAME | grep Volume
kubectl delete -f ./manifests/svc-nginx.yml
kubectl delete -f ./manifests/deployment-nginx.yml
kubectl delete -f ./manifests/pvc.yml
```

## JupyterHub

```bash
# https://hub.jupyter.org/helm-chart
helm repo add jupyterhub https://hub.jupyter.org/helm-chart && \
  helm repo update
```

```bash
export NAMESPACE=jupyterhub
export VERSION=3.0.0-beta.3.git.6238.habf210aa
export RELEASE=jupyterhub
```

```bash
# For KubeIngressProxy.
cat << EOF | kubectl apply -f -
kind: Role
apiVersion: rbac.authorization.k8s.io/v1
metadata:
  name: kube-ingress-proxy
rules:
  - apiGroups: [""]
    resources: ["endpoints", "services"]
    verbs: ["get", "watch", "list", "create", "update", "patch", "delete"]
  - apiGroups: ["networking.k8s.io"]
    resources: ["ingresses"]
    verbs: ["get", "watch", "list", "create", "update", "patch", "delete"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: kube-ingress-proxy-rolebinding-$NAMESPACE
  namespace: $NAMESPACE
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: Role
  name: kube-ingress-proxy
subjects:
  - kind: ServiceAccount
    name: hub
    namespace: $NAMESPACE
EOF
```

```bash
# Deploy.
# --set hub.image.tag=2023-07-11 \
# --set singleuser.image.tag=2023-07-11 \
# --set singleuser.networkTools.image.tag=2023-07-11 \
# --set prePuller.hook.image.tag=2023-07-11 \
# --values $DATALAYER_HOME/externals/jupyterhub-k8s/jupyterhub
helm upgrade \
  --install $RELEASE \
  jupyterhub/jupyterhub \
  --create-namespace \
  --namespace $NAMESPACE \
  --timeout 5m \
  --version $VERSION \
  --values $DATALAYER_HOME/src/tech/clouder/docs/docs/examples/manifests/jupyterhub-simple.yaml
```

```bash
# Check Helm Deployment.
helm ls -n $NAMESPACE
# Check K8S Deployment.
kubectl get pods -n $NAMESPACE -w
# hub-5474b656cf-22xp6     1/1     Running   0          2m39s
# proxy-6c766577b6-vqkxk   1/1     Running   0          2m39s
kubectl get svc -n $NAMESPACE
# hub            ClusterIP      10.111.230.197   <none>        8081/TCP                     2m43s
# proxy-api      ClusterIP      10.102.103.51    <none>        8001/TCP                     2m43s
# proxy-public   LoadBalancer   10.107.139.195   <pending>     80:30893/TCP,443:30125/TCP   2m43s
kubectl get ingress -n $NAMESPACE
# NAME         CLASS    HOSTS                  ADDRESS   PORTS   AGE
# jupyterhub   <none>   51.222.45.244.nip.io             80      6m28s
```

```bash
export JUPYTERHUB_PUBLIC_IP="$(kubectl get svc proxy-public -n $NAMESPACE --output jsonpath='{.status.loadBalancer.ingress[0].ip}')"
open http://$JUPYTERHUB_PUBLIC_IP.nip.io # user1 / datalayer
open http://$JUPYTERHUB_PUBLIC_IP.nip.io/hub/metrics
open http://$JUPYTERHUB_PUBLIC_IP # user1 / datalayer
```

```bash
# Shortcut for Minikube.
open $(minikube -n $NAMESPACE service proxy-public --url) # user1 / datalayer
```

```bash
# When you are done, delete your deployments.
helm delete $RELEASE -n $NAMESPACE && \
  kubectl delete namespace $NAMESPACE
```

## GPU

- https://help.ovhcloud.com/csm/en-public-cloud-kubernetes-deploy-gpu-application?id=kb_article_view&sysparm_article=KB0049719

```bash
helm repo add nvidia https://helm.ngc.nvidia.com/nvidia
helm repo update
helm install gpu-operator nvidia/gpu-operator -n gpu-operator --create-namespace --wait
kubectl get nodes --show-labels | grep "nvidia.com/gpu"
cat << EOF | kubectl apply -f -
apiVersion: v1
kind: Pod
metadata:
  name: cuda-vectoradd
spec:
  restartPolicy: OnFailure
  containers:
  - name: cuda-vectoradd
    image: "nvcr.io/nvidia/k8s/cuda-sample:vectoradd-cuda11.7.1"
    resources:
      limits:
         nvidia.com/gpu: 1
EOF
kubectl get pod -n default -w
kubectl logs cuda-vectoradd -n default
```

## Prometheus

- https://github.com/prometheus-community/helm-charts
- https://help.ovhcloud.com/csm/en-public-cloud-kubernetes-monitoring-apps-prometheus-grafana?id=kb_article_view&sysparm_article=KB0049906

```bash
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm repo update
```

```bash
helm upgrade \
  prometheus \
  --install prometheus-community/prometheus \
  --create-namespace \
  --namespace prometheus \
  --values $DATALAYER_HOME/etc/helm/prometheus/values.yaml
export POD_NAME=$(kubectl get pods --namespace prometheus -l "app.kubernetes.io/name=prometheus,app.kubernetes.io/instance=prometheus" -o jsonpath="{.items[0].metadata.name}")
kubectl --namespace prometheus port-forward $POD_NAME 9090
open http://localhost:9090
helm delete prometheus -n prometheus
```

```bash
# You can install only Prometheus without Grafana by setting the following property to false: --set grafana.enabled=false
helm upgrade \
  prometheus \
  --install prometheus-community/kube-prometheus-stack \
  --create-namespace \
  --namespace prometheus \
  --set prometheus.service.type=LoadBalancer \
  --set prometheus.prometheusSpec.serviceMonitorSelectorNilUsesHelmValues=false \
  --set grafana.service.type=LoadBalancer \
  --set grafana.adminpassword=my_cool_password \
  --values $DATALAYER_HOME/etc/helm/prometheus/values-stack.yaml
helm get values prometheus -n prometheus
kubectl get pods -n prometheus
kubectl get svc -n prometheus
#
export PROMETHEUS_URL=$(kubectl get svc -n prometheus -l app=kube-prometheus-stack-prometheus -o jsonpath='{.items[].status.loadBalancer.ingress[].ip}')
open http://$PROMETHEUS_URL:9090
# Login: admin / Password: prom-operator (by default)
# For example, you can click on the General/Kubernetes/Compute Resources/Cluster dashboard to visualize the metrics of your Kubernetes cluster:
export GRAFANA_URL=$(kubectl get svc -n prometheus -l app.kubernetes.io/name=grafana -o jsonpath='{.items[].status.loadBalancer.ingress[].ip}')
open http://$GRAFANA_URL
helm delete prometheus -n prometheus
```

## Prometheus GPU

- https://help.ovhcloud.com/csm/en-public-cloud-kubernetes-monitoring-gpu-application?id=kb_article_view&sysparm_article=KB0049912

```bash
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm repo update
helm inspect values prometheus-community/kube-prometheus-stack > /tmp/kube-prometheus-stack.values
# Before:
    additionalScrapeConfigs: []
# After:
    additionalScrapeConfigs:
    - job_name: gpu-metrics
      scrape_interval: 1s
      metrics_path: /metrics
      scheme: http
      kubernetes_sd_configs:
      - role: endpoints
        namespaces:
          names:
          - gpu-operator
      relabel_configs:
      - source_labels: [__meta_kubernetes_pod_node_name]
        action: replace
        target_label: kubernetes_node
# By default, Grafana password should be like this:
  adminPassword: prom-operator
helm install \
  prometheus \
  --namespace prometheus \
  --create-namespace \
  --install prometheus-community/kube-prometheus-stack \
  --set prometheus.service.type=LoadBalancer \
  --set prometheus.prometheusSpec.serviceMonitorSelectorNilUsesHelmValues=false \
  --set grafana.service.type=LoadBalancer \
  --values /tmp/kube-prometheus-stack.values
kubectl get pods -n prometheus
kubectl get svc -n prometheus
# You can check the GPU usage with several metrics in Prometheus:
# DCGM_FI_DEV_GPU_UTIL: GPU utilization.
# DCGM_FI_DEV_SM_CLOCK: SM clock frequency (in MHz).
# DCGM_FI_DEV_MEM_CLOCK: Memory clock frequency (in MHz).
# DCGM_FI_DEV_MEMORY_TEMP: Memory temperature (in C).
export PROMETHEUS_URL=$(kubectl get svc kube-prometheus-stack-1682-prometheus -n prometheus -o jsonpath='{.status.loadBalancer.ingress[0].ip}')
open http://$PROMETHEUS_URL:9090
# Import the NVIDIA dashboard from https://grafana.com/grafana/dashboards/12239, click on the Load button:
export GRAFANA_URL=$(kubectl get svc kube-prometheus-stack-1682576024-grafana -n prometheus -o jsonpath='{.status.loadBalancer.ingress[0].ip}')
open http://$GRAFANA_URL
```

## Renku Amalthea

```bash
helm repo add renku https://swissdatasciencecenter.github.io/helm-charts
helm repo update
helm upgrade --install amalthea \
  renku/amalthea \
  -n amalthea \
  --create-namespace
kubectl describe crd jupyterservers.amalthea.dev 
kubectl get all -n amalthea
cat << EOF | kubectl apply -f -
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: amalthea
  namespace: amalthea
  labels:
    app.kubernetes.io/name: amalthea
    app.kubernetes.io/instance: amalthea
    app.kubernetes.io/version: "latest"
rules:
  # Kopf: posting the events about the handlers progress/errors.
  - apiGroups: [""]
    resources: [events]
    verbs: [create, get, list, watch]
  # Amalthea: watching & handling for the custom resource we declare.
  - apiGroups: [amalthea.dev]
    resources: [jupyterservers]
    verbs: [get, list, watch, patch, delete]
  - apiGroups: [""]
    resources: [pods]
    verbs: [get, list, watch, delete]
  - apiGroups: [""]
    resources: [pods/exec]
    verbs: [create, get]
  # Amalthea get pod metrics used to cull idle Jupyter servers
  - apiGroups: ["metrics.k8s.io"]
    resources: [pods]
    verbs: [get, list, watch]
  # Amalthea: child resources we produce
  # Note that we do not patch/update/delete them ever.
  - apiGroups:
      - ""
      - apps
      - networking.k8s.io
    resources:
      - statefulsets
      - persistentvolumeclaims
      - services
      - ingresses
      - secrets
      - configmaps
    verbs: [create, get, list, watch]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: amalthea
  namespace: amalthea
  labels:
    app.kubernetes.io/name: amalthea
    app.kubernetes.io/instance: amalthea
    app.kubernetes.io/version: "latest"
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: Role
  name: amalthea
subjects:
  - kind: ServiceAccount
    name: amalthea
    namespace: amalthea
EOF
```

```bash
export INGRESS_NGINX_IP=$(kubectl get service ingress-nginx-controller -n ingress-nginx -o json | jq -r '.status.loadBalancer.ingress[].ip')
echo $INGRESS_NGINX_IP
kubectl apply -f $DATALAYER_HOME/etc/operator/renku-amalthea/notebook-1.yaml
kubectl describe jupyterserver notebook-1 -n amalthea
kubectl get jupyterserver -n amalthea -w
kubectl delete -f $DATALAYER_HOME/etc/operator/renku-amalthea/notebook-1.yaml
```

## Renku Lab

```bash
pip install renku
mkdir renku
cd renku
curl https://raw.githubusercontent.com/SwissDataScienceCenter/renku/master/scripts/generate-values/generate-values.sh -o generate-values.sh
sh generate-values.sh -o renku-values.yaml --gitlab
cat renku-values.yaml
helm repo add renku https://swissdatasciencecenter.github.io/helm-charts
helm repo update
helm upgrade --install renku \
  renku/renku \
 --namespace renku \
 --create-namespace \
 -f renku-values.yaml \
 --timeout 1800s
```

## Volume CSI Cinder

```bash
# kubectl apply -f https://raw.githubusercontent.com/kubernetes/cloud-provider-openstack/master/examples/cinder-csi-plugin/nginx.yaml
cat << EOF | kubectl apply -f -
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: myvol
spec:
  accessModes:
  - ReadWriteOnce
  resources:
    requests:
      storage: 1Gi
  storageClassName: csi-sc-cinderplugin
---
apiVersion: v1
kind: Pod
metadata:
  name: web
spec:
  containers:
    - name: web
      image: nginx
      ports:
        - name: web
          containerPort: 80
          hostPort: 8081
          protocol: TCP
      volumeMounts:
        - mountPath: "/usr/share/nginx/html"
          name: mypd
  volumes:
    - name: mypd
      persistentVolumeClaim:
        claimName: myvol
EOF
kubectl get pvc
kubectl get pv
kubectl get pod web
openstack volume list
openstack volume show 6b5f3296-b0eb-40cd-bd4f-2067a0d6287f
kubectl exec web -it -- bash
# df -k | grep /dev/sdb
# ls /dev/sdb
# mount | grep sdb
# fdisk -l /dev/sdb | grep Disk
# Mount disk
# lsblk
# sudo file -s /dev/sdb
# sudo mkfs -t ext4 /dev/sdb
# sudo mkdir /data
# sudo mount /dev/sdb /data
# df -k
# sudo cp /etc/fstab /etc/fstab.orig
# vi /etc/fstab
# /dev/sdb  /data  ext4  discard,errors=remount-ro       0 1
kubectl delete pod web
kubectl delete pvc myvol
```

## Octavia Load Balancer

```bash
kubectl run echoserver --image=gcr.io/google-containers/echoserver:1.10 --port=8080
cat <<EOF | kubectl apply -f -
---
kind: Service
apiVersion: v1
metadata:
  name: loadbalanced-service
spec:
  selector:
    run: echoserver
  type: LoadBalancer
  ports:
  - port: 80
    targetPort: 8080
    protocol: TCP
EOF
openstack loadbalancer list
kubectl get service loadbalanced-service -w
curl 51.210.144.59
kubectl delete svc loadbalanced-service
kubectl delete pod echoserver
```

Octavia Load Balancer (v2)

```bash
cat <<EOF | kubectl apply -f -
apiVersion: apps/v1
kind: Deployment
metadata:
  name: webserver
  namespace: default
  labels:
    app: webserver
spec:
  replicas: 1
  selector:
    matchLabels:
      app: webserver
  template:
    metadata:
      labels:
        app: webserver
    spec:
      containers:
      - name: webserver
        image: lingxiankong/alpine-test
        imagePullPolicy: IfNotPresent
        ports:
          - containerPort: 8080
EOF
kubectl expose deployment webserver --type=NodePort --target-port=8080
kubectl get svc
IP=10.99.195.15
curl http://$IP:8080
# webserver-58fcfb75fb-dz5kn
cat <<EOF | kubectl apply -f -
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: test-octavia-ingress
  annotations:
    kubernetes.io/ingress.class: "openstack"
    octavia.ingress.kubernetes.io/internal: "false"
spec:
  rules:
  - host: foo.bar.com
    http:
      paths:
      - path: /ping
        pathType: Exact
        backend:
          service:
            name: webserver
            port:
              number: 8080
EOF
kubectl get ingress
IP=192.168.168.177
curl -H "Host: foo.bar.com" http://$IP/ping
curl http://foo.bar.com/ping
# webserver-58fcfb75fb-dz5kn
```

Octavia Load Balancer (v3)

```bash
kubectl get nodes
kubectl get pods -A
cat << EOF | kubectl apply -f -
apiVersion: v1
kind: Service
metadata:
  name: hello-world
  labels:
    app: hello-world
spec:
  type: LoadBalancer
  ports:
  - port: 80
    targetPort: 80
    protocol: TCP
    name: http
  selector:
    app: hello-world
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: hello-world-deployment
  labels:
    app: hello-world
spec:
  replicas: 1
  selector:
    matchLabels:
      app: hello-world
  template:
    metadata:
      labels:
        app: hello-world
    spec:
      containers:
      - name: hello-world
        image: ovhplatform/hello
        ports:
        - containerPort: 80
EOF
kubectl get pods -n default -l app=hello-world
kubectl get deploy -n default -l app=hello-world
kubectl get services -n default -l app=hello-world
export SERVICE_URL=$(kubectl get svc hello-world -n default -o jsonpath='{.status.loadBalancer.ingress[].ip}')
open "http://$SERVICE_URL/"
kubectl delete services -n default -l app=hello-world
kubectl delete deploy -n default -l app=hello-world
```

Octavia Load Balancer (v4)

```bash
cat <<EOF | kubectl apply -f -
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: external-http-nginx-deployment
spec:
  replicas: 2
  selector:
    matchLabels:
      app: nginx
  template:
    metadata:
      labels:
        app: nginx
    spec:
      containers:
      - name: nginx
        image: nginx
        ports:
        - containerPort: 80
---
kind: Service
apiVersion: v1
metadata:
  name: external-http-nginx-service
  annotations:
    service.beta.kubernetes.io/openstack-internal-load-balancer: "false"
spec:
  selector:
    app: nginx
  type: LoadBalancer
  ports:
  - name: http
    port: 80
    targetPort: 80
EOF
```

## Octavia Load Balancer with Ingress

```bash
cat <<EOF | kubectl apply -f -
apiVersion: apps/v1
kind: Deployment
metadata:
  name: webserver
  namespace: default
  labels:
    app: webserver
spec:
  replicas: 1
  selector:
    matchLabels:
      app: webserver
  template:
    metadata:
      labels:
        app: webserver
    spec:
      containers:
      - name: webserver
        image: lingxiankong/alpine-test
        imagePullPolicy: IfNotPresent
        ports:
          - containerPort: 8080
---
apiVersion: v1
kind: Service
metadata:
  name: webserver-svc
  labels:
    app: webserver
spec:
  type: NodePort
  ports:
  - port: 8080
    name: webserver-http
    protocol: TCP
    targetPort: 8080
  selector:
    app: webserver
---
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: webserver-ingress
  annotations:
    kubernetes.io/ingress.class: "openstack"
spec:
  rules:
  - host: t1.gpu.io
    http:
      paths:
      - path: /
        pathType: Prefix
        backend:
          service:
            name: webserver-svc
            port:
              number: 8080
      - path: /test
        pathType: Prefix
        backend:
          service:
            name: webserver-svc
            port:
              number: 8080
EOF
kubectl get svc webserver-svc -w
NP_IP=10.102.77.141
curl http://$NP_IP:8080
# webserver-58fcfb75fb-dz5kn
LB_IP=141.94.208.196
curl -H "Host: t1.gpu.io" http://$LB_IP/ping
# webserver-58fcfb75fb-dz5kn
curl http://t1.gpu.io
kubectl get ingress webserver-ingress
# webserver-58fcfb75fb-dz5kn
```
