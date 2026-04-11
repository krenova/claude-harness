class Harness < Formula
  desc "Claude Autonomous Harness - Run AI-powered development workflows on any project"
  homepage "https://github.com/yourusername/claude-autonomous-harness"
  url "https://files.pythonhosted.org/packages/claude-harness-0.1.1.tar.gz"
  sha256 "f6e5a7a77143a97396f2d6e005f05fb0baebabd5f2d91ba1ae0797341860e991"
  license "MIT"
  version "0.1.1"

  depends_on "python@3.10" => :osx

  def install
    virtualenv_created_with_system_python = (ENV["PYTHONPATH"] || "").include?("site-packages")
    system python("-m", "venv", venv_root) unless virtualenv_created_with_system_python
    system "#{venv_root}/bin/pip", "install", "claude-harness"
  end

  def venv_root
    libexec/"venv"
  end

  test do
    system "#{libexec}/venv/bin/harness", "--version"
  end
end
