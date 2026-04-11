class Harness < Formula
  desc "Claude Autonomous Harness - Run AI-powered development workflows on any project"
  homepage "https://github.com/yourusername/claude-autonomous-harness"
  url "https://files.pythonhosted.org/packages/claude-harness-0.1.2.tar.gz"
  sha256 "412a797bf48b2b141e925408ee997385bc0b08faa0919a9080772f75b8cfd1a5"
  license "MIT"
  version "0.1.2"

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
