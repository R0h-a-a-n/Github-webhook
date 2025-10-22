pipeline {
    agent any

    environment {
        DOCKER_IMAGE = "spoofedbinary/github-monitor"
        BLUE_DEPLOY = "github-monitor-blue"
        GREEN_DEPLOY = "github-monitor-green"
        KUBE_CONFIG = credentials('kubeconfig-cred')
    }

    stages {
        stage('Checkout') {
            steps { git branch: 'main', url: 'https://github.com/R0h-a-a-n/Github-webhook.git' }
        }

        stage('Build Docker Image') {
            steps {
                script {
                    sh "docker build -t ${DOCKER_IMAGE}:${BUILD_NUMBER} ."
                }
            }
        }

        stage('Push Docker Image') {
            steps {
                withCredentials([usernamePassword(credentialsId: 'dockerhub-cred', usernameVariable: 'USER', passwordVariable: 'PASS')]) {
                    sh """
                      echo $PASS | docker login -u $USER --password-stdin
                      docker push ${DOCKER_IMAGE}:${BUILD_NUMBER}
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
                        echo "Current live: ${currentColor}, deploying: ${newColor}"

                        sh """
                        kubectl set image deployment/github-monitor-${newColor} github-monitor=${DOCKER_IMAGE}:${BUILD_NUMBER} || \
                        kubectl apply -f k8s/deployment-${newColor}.yaml
                        """

                        sh "kubectl rollout status deployment/github-monitor-${newColor}"
                        sh "kubectl patch svc github-monitor-service -p '{\"spec\":{\"selector\":{\"app\":\"github-monitor-${newColor}\"}}}'"
                        sh "kubectl delete deployment ${currentColor} || true"
                    }
                }
            }
        }
    }
}
