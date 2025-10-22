pipeline {
    agent any

    environment {
        DOCKER_PATH = '/opt/homebrew/bin/docker'
        KUBECTL_PATH = '/opt/homebrew/bin/kubectl'
        DOCKER_IMAGE = 'spoofedbinary/github-monitor'
        BLUE_DEPLOY = 'github-monitor-blue'
        GREEN_DEPLOY = 'github-monitor-green'
    }

    stages {

        stage('Pre-Docker Setup') {
            steps {
                sh '''
                    mkdir -p ~/.docker
                    echo '{"credsStore":""}' > ~/.docker/config.json
                '''
            }
        }

        stage('Checkout') {
            steps {
                git branch: 'main', url: 'https://github.com/R0h-a-a-n/Github-webhook.git'
            }
        }

        stage('Docker Login') {
            steps {
                withCredentials([usernamePassword(credentialsId: 'dockherhub_cred', usernameVariable: 'DOCKER_USER', passwordVariable: 'DOCKER_PASS')]) {
                    sh '''
                        echo $DOCKER_PASS | ${DOCKER_PATH} login -u $DOCKER_USER --password-stdin
                    '''
                }
            }
        }

        stage('Build Docker Image') {
            steps {
                sh '''
                    ${DOCKER_PATH} build -t ${DOCKER_IMAGE}:${BUILD_NUMBER} .
                '''
            }
        }

        stage('Push Docker Image') {
            steps {
                sh '''
                    ${DOCKER_PATH} push ${DOCKER_IMAGE}:${BUILD_NUMBER}
                '''
            }
        }

        stage('Deploy Blue-Green') {
            steps {
                withCredentials([file(credentialsId: 'kubeconfig-cred', variable: 'KUBECONFIG')]) {
                    script {
                        def KUBECTL = "${KUBECTL_PATH}"

                        def currentColor = sh(
                            script: "${KUBECTL} get svc github-monitor-service -o=jsonpath='{.spec.selector.app}' || echo github-monitor-green",
                            returnStdout: true
                        ).trim()

                        def newColor = currentColor.contains('blue') ? 'green' : 'blue'
                        echo "Deploying ${newColor}"

                        sh """
                            export PATH=$PATH:/opt/homebrew/bin
                            ${KUBECTL} set image deployment/github-monitor-${newColor} github-monitor=${DOCKER_IMAGE}:${BUILD_NUMBER} || \
                            ${KUBECTL} apply -f k8s/deployment-${newColor}.yaml
                            ${KUBECTL} rollout status deployment/github-monitor-${newColor}
                            ${KUBECTL} patch svc github-monitor-service -p '{"spec":{"selector":{"app":"github-monitor-${newColor}"}}}'
                            ${KUBECTL} delete deployment ${currentColor} || true
                        """
                    }
                }
            }
        }
    }
}
