pipeline {
    agent any
    environment {
        DOCKER_PATH = '/opt/homebrew/bin/docker'
        DOCKER_IMAGE = 'spoofedbinary/github-monitor'
        BLUE_DEPLOY = 'github-monitor-blue'
        GREEN_DEPLOY = 'github-monitor-green'
        KUBE_CONFIG = credentials('kubeconfig-cred')
    }

    stages {
        stage('Checkout') {
            steps {
                git branch: 'main', url: 'https://github.com/R0h-a-a-n/Github-webhook.git'
            }
        }

        stage('Build Docker Image') {
            steps {
                script {
                    sh "${DOCKER_PATH} build -t ${DOCKER_IMAGE}:${BUILD_NUMBER} ."
                }
            }
        }

        stage('Push Docker Image') {
            steps {
                withCredentials([usernamePassword(credentialsId: 'dockerhub-cred', usernameVariable: 'USER', passwordVariable: 'PASS')]) {
                    sh """
                        echo $PASS | ${DOCKER_PATH} login -u $USER --password-stdin
                        ${DOCKER_PATH} push ${DOCKER_IMAGE}:${BUILD_NUMBER}
                    """
                }
            }
        }

        stage('Deploy Blue-Green') {
            steps {
                withCredentials([file(credentialsId: 'kubeconfig-cred', variable: 'KUBECONFIG')]) {
                    script {
                        def currentColor = sh(script: "kubectl get svc github-monitor-service -o=jsonpath='{.spec.selector.app}' || echo github-monitor-green", returnStdout: true).trim()
                        def newColor = currentColor.contains('blue') ? 'green' : 'blue'
                        echo "Deploying ${newColor}"

                        sh """
                            kubectl set image deployment/github-monitor-${newColor} github-monitor=${DOCKER_IMAGE}:${BUILD_NUMBER} || \
                            kubectl apply -f k8s/deployment-${newColor}.yaml
                            kubectl rollout status deployment/github-monitor-${newColor}
                            kubectl patch svc github-monitor-service -p '{"spec":{"selector":{"app":"github-monitor-${newColor}"}}}'
                            kubectl delete deployment ${currentColor} || true
                        """
                    }
                }
            }
        }
    }
}
